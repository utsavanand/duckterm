import { describe, expect, it } from "vitest";
import {
  applyAll,
  applyEvent,
  contextLevel,
  contextWindowFor,
  contextWindowIsAssumed,
  effectiveState,
  IDLE_SETTLE_MS,
} from "./sessions";
import { DucktermEvent, SessionView } from "./types";

function ev(
  e: Partial<DucktermEvent> & { event_type: string },
): DucktermEvent {
  return { session_key: "s1", _id: "e", _ts: 1000, ...e } as DucktermEvent;
}

const empty = () => new Map<string, SessionView>();

describe("applyEvent", () => {
  it("creates a session from its first event, deriving the label from source_app", () => {
    const after = applyEvent(
      empty(),
      ev({ event_type: "SessionStart", source_app: "myrepo" }),
    );

    const s = after.get("s1")!;
    expect(s.label).toBe("myrepo");
    expect(s.state).toBe("busy");
    expect(s.eventCount).toBe(1);
  });

  it("keeps a session launched once seen launched, even if a later event omits it", () => {
    const launched = applyEvent(
      empty(),
      ev({ event_type: "SessionStart", launched: true }),
    );

    const after = applyEvent(
      launched,
      ev({ event_type: "PreToolUse", tool_name: "Bash" }),
    );

    expect(after.get("s1")!.launched).toBe(true);
  });

  it("marks a launched session ptyOwned so its terminal shows before the first seed", () => {
    // Regression: a freshly launched session arrives via live events before the
    // next /sessions seed. applyEvent must derive ptyOwned (it once didn't), or
    // the terminal pane is hidden with "isn't running in a terminal we own".
    const after = applyEvent(
      empty(),
      ev({ event_type: "SessionStart", launched: true }),
    );
    expect(after.get("s1")!.ptyOwned).toBe(true);
  });

  it("does not rename the session from source_app on later events", () => {
    const first = applyEvent(
      empty(),
      ev({ event_type: "SessionStart", source_app: "myrepo" }),
    );

    // An agent cd-ing into web/ would otherwise rename the session to "web".
    const after = applyEvent(
      first,
      ev({ event_type: "PreToolUse", source_app: "web" }),
    );

    expect(after.get("s1")!.label).toBe("myrepo");
  });

  it("stamps idleSince on Stop and clears it on the next activity", () => {
    const stopped = applyEvent(empty(), ev({ event_type: "Stop", _ts: 5000 }));
    expect(stopped.get("s1")!.idleSince).toBe(5000);

    const active = applyEvent(
      stopped,
      ev({ event_type: "PreToolUse", _ts: 6000 }),
    );
    expect(active.get("s1")!.idleSince).toBeUndefined();
  });

  it("keeps a stopped session at rest until an explicit SessionStart", () => {
    const stopped = applyEvent(
      empty(),
      ev({ event_type: "Notification", lifecycle: "stopped" }),
    );
    expect(stopped.get("s1")!.state).toBe("stopped");

    // A stray late event (e.g. a SessionEnd) must not flip it to terminated.
    const stray = applyEvent(stopped, ev({ event_type: "SessionEnd" }));
    expect(stray.get("s1")!.state).toBe("stopped");

    // Only a SessionStart revives it.
    const revived = applyEvent(stopped, ev({ event_type: "SessionStart" }));
    expect(revived.get("s1")!.state).toBe("busy");
  });

  it("reads events the way the server does: auto-reviewed requests stay busy, idle notices are idle", () => {
    const state = (e: Partial<DucktermEvent>) =>
      applyEvent(empty(), ev({ event_type: "PermissionRequest", ...e })).get("s1")!.state;
    expect(state({ auto_reviewed: true })).toBe("busy"); // Codex's reviewer settles it
    expect(state({ event_type: "Notification", notification_type: "permission_prompt" })).toBe("waiting");
    expect(state({ event_type: "Notification", notification_type: "idle_prompt" })).toBe("idle");
    expect(state({ event_type: "Notification", message: "Claude is waiting for your input" })).toBe("idle");
  });

  it("maps permission requests to waiting and SessionEnd to terminated", () => {
    expect(
      applyEvent(empty(), ev({ event_type: "PermissionRequest" })).get("s1")!
        .state,
    ).toBe("waiting");
    expect(
      applyEvent(empty(), ev({ event_type: "SessionEnd" })).get("s1")!.state,
    ).toBe("terminated");
  });

  it("ignores an event with no session key", () => {
    const before = empty();
    const after = applyEvent(before, {
      event_type: "Stop",
      _id: "x",
      _ts: 1,
    } as DucktermEvent);
    expect(after).toBe(before);
  });
});

describe("effectiveState", () => {
  const base: SessionView = {
    key: "s1",
    label: "s1",
    state: "busy",
    lastEventType: "",
    startedAt: 0,
    updatedAt: 0,
    eventCount: 1,
  };

  it("settles a stopped-but-busy session to idle only after the grace window", () => {
    const s = { ...base, state: "busy" as const, idleSince: 1000 };

    expect(effectiveState(s, 1000 + IDLE_SETTLE_MS - 1)).toBe("busy");
    expect(effectiveState(s, 1000 + IDLE_SETTLE_MS)).toBe("idle");
  });

  it("returns terminal/at-rest states immediately without the grace", () => {
    for (const state of [
      "terminated",
      "stopped",
      "archived",
      "waiting",
    ] as const) {
      expect(effectiveState({ ...base, state }, 1e15)).toBe(state);
    }
  });
});

describe("applyAll", () => {
  it("folds a sequence of events into the final session state", () => {
    const map = applyAll([
      ev({ event_type: "SessionStart", launched: true }),
      ev({ event_type: "PreToolUse" }),
      ev({ event_type: "Stop", _ts: 9000 }),
    ]);

    const s = map.get("s1")!;
    expect(s.launched).toBe(true);
    expect(s.eventCount).toBe(3);
    expect(s.idleSince).toBe(9000);
  });
});

describe("contextLevel (model-aware windows)", () => {
  it("does not warn a 1M-window model at 123k", () => {
    expect(contextLevel(123_000, "claude-fable-5")).toBeNull();
  });
  it("does not warn on an assumed window", () => {
    // An unrecognized model's 200k is a guess. Warning off a guess fired
    // "high" on every opus-5 session past 160k against a real 1M window.
    expect(contextLevel(123_000, "claude-sonnet-4")).toBeNull();
    expect(contextLevel(165_000, "claude-sonnet-4")).toBeNull();
    expect(contextLevel(123_000, undefined)).toBeNull();
  });
  it("scales thresholds for large windows", () => {
    expect(contextLevel(560_000, "claude-fable-5")).toBe("warm"); // >55% of 1M
    expect(contextLevel(820_000, "claude-mythos-5")).toBe("high"); // >80%
  });
  it("does not call an opus-5 session at 559k 'high'", () => {
    // The reported bug: 559k showed "0 left" and a high warning, because the
    // window was assumed to be 200k. Against the real 1M window 559k is 56%,
    // so "warm" is correct and "high" would be the bug.
    expect(contextLevel(559_000, "claude-opus-5")).toBe("warm");
    expect(contextLevel(559_000, "claude-opus-5-5")).toBe("warm");
    expect(contextLevel(300_000, "claude-opus-5")).toBeNull(); // 30%: no warning
  });
});

describe("context window resolution", () => {
  it("gives opus-5 and fable the 1M window", () => {
    expect(contextWindowFor("claude-opus-5")).toBe(1_000_000);
    expect(contextWindowFor("claude-opus-5-5")).toBe(1_000_000);
    expect(contextWindowFor("claude-fable-5")).toBe(1_000_000);
  });
  it("leaves 559k of headroom instead of reporting none", () => {
    // The exact figure from the owner's screenshot: "559k used - 0 left".
    const left = contextWindowFor("claude-opus-5") - 559_000;
    expect(left).toBe(441_000);
  });
  it("marks an unrecognized model's window as assumed", () => {
    expect(contextWindowIsAssumed("some-new-model", 10_000)).toBe(true);
    expect(contextWindowIsAssumed(undefined, 10_000)).toBe(true);
    expect(contextWindowIsAssumed("claude-opus-5", 10_000)).toBe(false);
  });
  it("treats used-exceeding-window as proof the window is wrong", () => {
    // Silently clamping this to "0 left" is what hid the bug.
    expect(contextWindowIsAssumed("claude-opus-5", 1_200_000)).toBe(true);
  });
});

describe("label precedence (renames must survive restarts)", () => {
  it("a replayed event's launch name cannot override an established label", () => {
    let m = new Map<string, SessionView>();
    m.set("k", { key: "k", label: "main-dev" } as SessionView);
    m = applyEvent(m, {
      event_type: "SessionStart",
      session_key: "k",
      name: "Main", // the original launch name, replayed over SSE on restart
      _ts: 1,
    } as unknown as DucktermEvent);
    expect(m.get("k")!.label).toBe("main-dev");
  });

  it("an event name still labels a brand-new session", () => {
    const m = applyEvent(
      new Map(),
      {
        event_type: "SessionStart",
        session_key: "k",
        name: "Main",
        _ts: 1,
      } as unknown as DucktermEvent,
    );
    expect(m.get("k")!.label).toBe("Main");
  });
});
