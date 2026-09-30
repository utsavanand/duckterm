import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { poseFor } from "./Duck";
import { applyEvent, effectiveState, IDLE_SETTLE_MS } from "./sessions";
import { DucktermEvent, SessionView, viewFromPersisted } from "./types";
import { ATTEND_AFTER_MS, useAttended } from "./useAttended";

vi.mock("./api", () => ({ api: { sessionAttended: vi.fn() } }));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

const ev = (event_type: string, _ts: number): DucktermEvent => ({ _id: `${_ts}`, _ts, session_key: "s", event_type });
const fold = (events: DucktermEvent[]) => events.reduce(applyEvent, new Map<string, SessionView>()).get("s")!;

it("keeps the hand up through the agent's next events until the owner attends", () => {
  const asked = fold([ev("SessionStart", 1), ev("PermissionRequest", 2), ev("PreToolUse", 3), ev("Stop", 4)]);
  expect(asked.attentionSince).toBe(2);
  expect(poseFor(effectiveState(asked, 4 + IDLE_SETTLE_MS), !!asked.attentionSince)).toBe("waiting");
  const attended = applyEvent(new Map([["s", asked]]), ev("Attended", 5)).get("s")!;
  expect(attended.attentionSince).toBeUndefined();
  expect([attended.state, attended.updatedAt, attended.eventCount, attended.idleSince]).toEqual([asked.state, asked.updatedAt, asked.eventCount, asked.idleSince]);
  expect(poseFor(effectiveState(attended, 4 + IDLE_SETTLE_MS), false)).toBe("idle");
});

it("reads a raised hand from the server after a reload", () => {
  const view = viewFromPersisted({ session_key: "s", state: "busy", updated_at: 9, started_at: 1, event_count: 3, attention_since: 7 } as never);
  expect(view.attentionSince).toBe(7);
});

it("never shows a hand on a duck at rest", () => {
  expect(poseFor("archived", true)).toBe("sleeping");
  expect(poseFor("busy", false)).toBe("busy");
});

it("settles to idle 30 seconds after a turn ends, not 5 minutes", () => {
  const done = fold([ev("SessionStart", 0), ev("Stop", 1000)]);
  expect(effectiveState(done, 1000 + 29_000)).toBe("busy");
  expect(effectiveState(done, 1000 + 30_000)).toBe("idle");
  expect(effectiveState(done, 1000 + 30_000, 90_000)).toBe("busy"); // voice's longer hold
});

function Probe({ id, raised }: { id: string | null; raised: boolean }) {
  useAttended(id, raised);
  return null;
}

it("tells the server once a raised session has been open for a moment", () => {
  vi.useFakeTimers();
  vi.mocked(api.sessionAttended).mockResolvedValue({ attended: true });
  const view = render(<Probe id="s" raised />);
  vi.advanceTimersByTime(ATTEND_AFTER_MS - 1);
  expect(api.sessionAttended).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1);
  expect(api.sessionAttended).toHaveBeenCalledWith("s");
  view.rerender(<Probe id="t" raised={false} />);
  vi.advanceTimersByTime(ATTEND_AFTER_MS * 2);
  expect(api.sessionAttended).toHaveBeenCalledTimes(1); // a session without a raised hand isn't reported
});
