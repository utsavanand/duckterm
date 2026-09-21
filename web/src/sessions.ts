import {
  DucktermEvent,
  SessionState,
  SessionView,
  repoNameFrom,
  sessionKeyOf,
} from "./types";

// After a Stop, a session keeps reading "busy" for this long before settling to
// idle — so a session in active back-and-forth doesn't read idle between turns.
export const IDLE_SETTLE_MS = 5 * 60_000;

function deriveState(e: DucktermEvent, prev?: SessionState): SessionState {
  // An explicit lifecycle marker (deliberate stop/archive/sweep) always wins.
  if (e.lifecycle === "archived") return "archived";
  if (e.lifecycle === "stopped") return "stopped";
  // A stopped/archived session is at rest: only an explicit resume
  // (SessionStart) revives it. A stray late event — including a resumed-then-
  // exited agent's SessionEnd — must not flip it. This runs before the
  // terminated rule on purpose (mirrors the server).
  if (
    (prev === "stopped" || prev === "archived") &&
    e.event_type !== "SessionStart"
  )
    return prev;
  if (e.lifecycle === "terminated" || e.event_type === "SessionEnd")
    return "terminated";
  switch (e.event_type) {
    case "PermissionRequest":
    case "Notification":
      return "waiting";
    case "PreToolUse":
    case "PostToolUse":
    case "UserPromptSubmit":
    case "SessionStart":
      // PostToolUse means a tool just finished — the agent is still mid-turn and
      // about to do more. Only `Stop` (turn ended) drops it to idle. Treating
      // PostToolUse as idle made sessions flap busy↔idle on every tool call.
      return "busy";
    case "Stop":
      // Stays busy now; effectiveState() flips it to idle after the settle grace.
      return "busy";
    default:
      return prev ?? "busy";
  }
}

/** The state to display/filter on, applying the post-Stop settling grace. */
export function effectiveState(s: SessionView, now: number): SessionState {
  if (
    s.state === "terminated" ||
    s.state === "stopped" ||
    s.state === "archived" ||
    s.state === "waiting"
  )
    return s.state;
  if (s.idleSince !== undefined && now - s.idleSince >= IDLE_SETTLE_MS)
    return "idle";
  return s.state;
}

/** Fold an event into the session map. Pure; returns a new map. */
export function applyEvent(
  sessions: Map<string, SessionView>,
  e: DucktermEvent,
): Map<string, SessionView> {
  const key = sessionKeyOf(e);
  if (!key) return sessions;

  const next = new Map(sessions);
  const prev = next.get(key);
  next.set(key, {
    // Preserve fields seeded from /sessions (metrics, intention, repoName, …);
    // only overwrite what this event actually carries.
    ...prev,
    key,
    // An ESTABLISHED label wins over event names: renames go through PATCH +
    // the /sessions seed, while the SSE replay re-delivers old SessionStart
    // events whose `name` is the original launch name — letting those win
    // reverted every rename on restart. Event names only label brand-new
    // sessions. (And never re-derive from e.source_app on later events:
    // it's the cwd basename, so a cd into web/ would rename the session.)
    label:
      prev?.label ||
      e.name ||
      e.session_name ||
      e.source_app ||
      key.slice(0, 8),
    state: deriveState(e, prev?.state),
    // Stamp when the agent stopped; clear it on any new activity. effectiveState
    // uses this to settle to idle only after a quiet grace period.
    idleSince:
      e.event_type === "Stop"
        ? e._ts
        : e.event_type === "SessionEnd"
          ? prev?.idleSince
          : undefined,
    lastEventType: e.event_type ?? prev?.lastEventType ?? "",
    lastTool: e.tool_name ?? prev?.lastTool,
    // Don't let a live event without these fields overwrite the seeded values.
    cwd: prev?.cwd ?? e.cwd,
    branch: prev?.branch ?? e.branch,
    // Keep the runtime once known: a session born from a live event (a launched
    // session's SessionStart) must capture it, or the conversation-fork option
    // (gated on runtime === "claude-code") stays disabled.
    runtime: prev?.runtime ?? e.runtime,
    repoName: prev?.repoName ?? repoNameFrom(e.repo_path, e.source_app),
    worktreePath: prev?.worktreePath ?? e.worktree_path,
    parentKey: prev?.parentKey ?? e.parent_session_key,
    // Sticky: once a session is known launched, stay launched — a later watched
    // hook event for the same key can't downgrade it.
    launched: prev?.launched || e.launched === true,
    // In-process launches (the default) emit launched:true and Duckterm owns
    // their PTY. Tab launches (API in_terminal:true) also emit launched:true
    // but stamp pty_owned:false — without that check, the live event would
    // mount a dead black terminal for a session running in iTerm/Terminal.
    ptyOwned: prev?.ptyOwned || (e.launched === true && e.pty_owned !== false),
    contextTokens: prev?.contextTokens,
    model: prev?.model,
    metaHarnesses: prev?.metaHarnesses,
    startedAt: prev?.startedAt ?? e._ts,
    updatedAt: e._ts,
    eventCount: (prev?.eventCount ?? 0) + 1,
  });
  return next;
}

export function applyAll(events: DucktermEvent[]): Map<string, SessionView> {
  return events.reduce(applyEvent, new Map<string, SessionView>());
}


// The standard claude context window; "left" in the panel is approximate
// (the 1M-beta window would read pessimistically, never optimistically).
// Per-model context windows. A hardcoded 200k flagged a fable-5 session
// (1M window) as nearly full at 123k. Unknown models keep the pessimistic
// 200k default — warning too early beats never warning.
const MODEL_WINDOWS: [RegExp, number][] = [[/fable|mythos/i, 1_000_000]];

export function contextWindowFor(model?: string): number {
  for (const [re, win] of MODEL_WINDOWS) {
    if (model && re.test(model)) return win;
  }
  return 200_000;
}

// Context-size thresholds (claude's window is ~200k): "warm" = start thinking
// about a checkpoint; "high" = checkpoint or /compact now, quality degrades
// as the auto-compact cliff approaches.
export function contextLevel(
  tokens?: number,
  model?: string,
): "warm" | "high" | null {
  if (!tokens) return null;
  const win = contextWindowFor(model);
  if (tokens >= win * 0.8) return "high";
  if (tokens >= win * 0.55) return "warm";
  return null;
}

export function fmtTokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}
