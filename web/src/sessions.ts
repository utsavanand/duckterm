import {
  DucktermEvent,
  SessionState,
  SessionView,
  repoNameFrom,
  sessionKeyOf,
  waitingCauseOf,
} from "./types";

// After a Stop, a session keeps reading "busy" for this long before settling to
// idle, so back-and-forth between turns doesn't flicker. Owner decision,
// 2026-09-30: 30 s, down from 5 minutes, which kept ducks typing and
// celebrations waiting long after the agent finished. Only a Stop starts it,
// so a pause inside a turn can't make a hook-driven duck look idle.
export const IDLE_SETTLE_MS = 30_000;

function deriveState(e: DucktermEvent, prev?: SessionState): SessionState {
  // An explicit lifecycle marker (deliberate stop/archive/sweep) always wins.
  if (e.lifecycle === "archived") return "archived";
  if (e.lifecycle === "stopped") return "stopped";
  if (e.lifecycle === "interrupted") return "interrupted";
  // A stopped/interrupted/archived session is at rest: only an explicit resume
  // (SessionStart) revives it. A stray late event — including a resumed-then-
  // exited agent's SessionEnd — must not flip it. This runs before the
  // terminated rule on purpose (mirrors the server).
  if (
    (prev === "stopped" || prev === "interrupted" || prev === "archived") &&
    e.event_type !== "SessionStart"
  )
    return prev;
  if (e.lifecycle === "terminated" || e.event_type === "SessionEnd")
    return "terminated";
  // Mirrors the server's derive_state (persistence/history.py). The server
  // tags these events before they reach the stream, so the badge the stream
  // drives must read them the same way or the two disagree.
  switch (e.event_type) {
    case "PermissionRequest":
      return e.auto_reviewed ? "busy" : "waiting";
    case "Notification":
      if (e.notification_type === "idle_prompt" || (e.message ?? "").startsWith("Claude is waiting for your input"))
        return "idle";
      if (e.notification_type === "auth_success") return prev ?? "busy";
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
export function effectiveState(s: SessionView, now: number, settleMs = IDLE_SETTLE_MS): SessionState {
  if (
    s.state === "terminated" ||
    s.state === "stopped" ||
    s.state === "interrupted" ||
    s.state === "archived" ||
    s.state === "waiting"
  )
    return s.state;
  if (s.idleSince !== undefined && now - s.idleSince >= settleMs)
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
  // Server bookkeeping for the merge checkpoint; not agent activity, so it
  // must not clear the settle time a Stop just set.
  if (e.event_type === "MergeDelivered") return sessions;
  if (e.event_type === "Attended") {
    // The owner attended to it: only the raised hand drops. Not agent activity.
    if (prev) next.set(key, { ...prev, attentionSince: undefined });
    return next;
  }
  const state = deriveState(e, prev?.state);
  const stillWaiting = state === "waiting" && prev?.state === "waiting";
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
    state,
    // A wait keeps its start time until the session stops waiting, so voice
    // mode can tell one long wait from a new one.
    waitingSince: state !== "waiting" ? undefined : stillWaiting ? prev?.waitingSince ?? e._ts : e._ts,
    // Raised when it starts waiting; only the owner attending lowers it.
    attentionSince: prev?.attentionSince ?? (state === "waiting" && prev?.state !== "waiting" ? e._ts : undefined),
    waitingCause:
      state !== "waiting"
        ? undefined
        : stillWaiting && prev?.waitingCause
          ? prev.waitingCause
          : waitingCauseOf(e.event_type, e.notification_type, e.tool_name),
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
    // A confirmed absence from /sessions or deletion must survive old fork events.
    parentKey: prev?.parentKey !== undefined ? prev.parentKey : e.parent_session_key,
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


// Per-model context windows. A hardcoded 200k flagged a fable-5 session
// (1M window) as nearly full at 123k. Unknown models keep a 200k default for
// the number, but it renders as "window unknown" and never drives a warning
// (see contextWindowIsAssumed).
const MODEL_WINDOWS: [RegExp, number][] = [
  [/fable|mythos/i, 1_000_000],
  [/opus-5/i, 1_000_000],
];

export const DEFAULT_WINDOW = 200_000;

export function contextWindowFor(model?: string): number {
  for (const [re, win] of MODEL_WINDOWS) {
    if (model && re.test(model)) return win;
  }
  return DEFAULT_WINDOW;
}

/** True when the window is a guess rather than a known per-model value.
 *
 * A guess must not render like a fact. An unrecognized model showed a
 * confident "0 left" for every opus-5 session — 559k used against an assumed
 * 200k window, clamped to zero — and the same assumption drove a permanent
 * "high" context warning. Used exceeding the window is itself proof the
 * guess is wrong, so that counts as unknown too. */
export function contextWindowIsAssumed(model?: string, tokens?: number): boolean {
  const matched = MODEL_WINDOWS.some(([re]) => model && re.test(model));
  if (!matched) return true;
  return tokens != null && tokens > contextWindowFor(model);
}

// Context-size thresholds (claude's window is ~200k): "warm" = start thinking
// about a checkpoint; "high" = checkpoint or /compact now, quality degrades
// as the auto-compact cliff approaches.
export function contextLevel(
  tokens?: number,
  model?: string,
): "warm" | "high" | null {
  if (!tokens) return null;
  // Never warn off an assumed window: that fired "high" on every opus-5
  // session past 160k against a window that was actually 1M.
  if (contextWindowIsAssumed(model, tokens)) return null;
  const win = contextWindowFor(model);
  if (tokens >= win * 0.8) return "high";
  if (tokens >= win * 0.55) return "warm";
  return null;
}

export function fmtTokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}
