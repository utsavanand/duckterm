# Performance: why typing gets slower as the fleet grows

Status: investigation 2026-09-27, at the owner's request. Findings from
reading the code and measuring the live instance. Not yet fixed.

The owner: "as I've been running a lot of sessions and adding a lot of
artifacts, the typing on the terminal is getting slower."

Typing latency is the worst thing to degrade — it is felt on **every
keystroke**, and it undermines the product's core claim of being a real
terminal rather than a transcript viewer.

## Measured scale on the live instance

| | A week ago | Now |
| --- | --- | --- |
| Live sessions | ~15 | **23** |
| Artifacts | 0 | 44 |
| Events | ~17,500 | **30,286** |
| Database | 6 MB | **42 MB** |

Nothing here is large in absolute terms. The problem is not data volume; it
is per-session work multiplied by session count.

## Finding 1 — every PTY terminal stays mounted, always (the main cause)

`App.tsx` renders **all** `ptyOwned` sessions at once and hides the
unselected ones with `display: none`:

```tsx
{terminalAgents.map((s) => (
  <div style={{ display: view === "terminal" && s.key === selectedKey ? "flex" : "none" }}>
    <Terminal sessionKey={s.key} active={…} theme={themeFor(s)} />
```

The comment explains why, and the reasoning is sound: *"Re-mounting on every
switch would reconnect the WS and replay the whole buffer from scratch each
time."* Instant switching is a real feature.

But the cost scales with the fleet. At 23 sessions that is **23 live xterm.js
instances, 23 open WebSockets, and 23 parsers** all decoding output
continuously — including for sessions the user cannot see. A busy agent
hidden behind `display: none` still parses every byte, still allocates, still
holds 5,000 lines of scrollback. The foreground terminal competes with 22
background ones for the same main thread, and keystroke handling is on that
thread.

**This is the one that matches the owner's symptom**: it gets worse
monotonically with session count, and it is worst when other agents are
busy.

## Finding 2 — a 1 Hz re-render of the whole dashboard

`const now = useNow(1000)` re-renders `Dashboard` every second to refresh
relative timestamps. On each render, `sessions` is rebuilt as a **new array
of new objects**:

```tsx
const sessions = sourceSessions.map((s) => ({ ...s, inboxPending: … }));
```

so every derived value and child prop changes identity once per second, and
**`Terminal` is not memoized** (no `React.memo` anywhere in the file). React
must therefore reconcile all 23 terminal slots every second. It does not
re-create the xterm instances — the `key` is stable — but it does the
reconciliation work, and any non-memoized child does its render work too.

## Finding 3 — polling stacks up

Concurrent timers in the UI: approvals every 2 s, Messages every 3 s, inbox
counts ~3 s, history every 10 s, the event-stream seed every 30 s, plus the
1 Hz clock. Each fires independently of the others and of whether its panel
is visible.

## Finding 4 — `/sessions` does per-session disk work on every call

For each row, the server calls `_transcript_stats_for` (locates and reads the
tail of the agent's transcript **file on disk**), `_suites_for` (inspects the
session's directory), and `_reconcile_waiting` (reads the live tmux screen
for pty-owned sessions). That is up to three filesystem touches per session
per request — ~69 at 23 sessions — and the dashboard re-seeds this every
30 s. The work is all cheap individually; it is the multiplication that
bites, and it grows as sessions are added.

## What to do, in order

1. **Cap mounted terminals (biggest win, moderate effort).** Keep the
   selected terminal plus the N most recently used mounted; unmount the
   rest and remount on demand. The instant-switch property is preserved for
   the sessions a user actually cycles between, which is a handful, not 23.
   Measure first: instrument keystroke-to-render latency at 5 / 15 / 23
   mounted terminals so the fix is proven rather than assumed.
2. **Memoize `Terminal` and stabilize `sessions` identity.** `React.memo` on
   `Terminal`, and derive `sessions` inside a `useMemo` keyed on the real
   inputs so a clock tick does not produce a new array. Cheap, low risk.
3. **Decouple the clock from the dashboard.** The 1 Hz tick exists for
   relative timestamps; scope it to the components that display them rather
   than re-rendering the tree.
4. **Make polling visibility-aware.** A hidden Messages panel does not need
   a 3-second fetch; a background tab needs none of them. Pause on
   `document.hidden` and when the panel is not rendered.
5. **Cache `/sessions` per-row work.** Transcript stats and suite detection
   change slowly; cache with a short TTL keyed on session + mtime instead of
   re-reading on every seed.

## Explicitly not the fix

- **Rewriting the byte pump in Go/Rust.** The documented trigger for that
  (architecture.md) is profiling showing the pump is the bottleneck at ~20+
  busy terminals. We are at 23 — so this is the first time that trigger is
  even testable — but findings 1–3 are all *client-side*, and the symptom is
  typing latency in the browser. Fix the client first and re-measure; a
  sidecar would not help a main thread busy reconciling 23 React subtrees.
- **Pruning the database.** 42 MB of SQLite is not causing keystroke lag,
  and the owner has said backups must stay retained. Do not "optimize" by
  deleting history.

## Deliverable

A measurement of keystroke-to-paint latency against mounted-terminal count,
then fixes 1–3 with that measurement repeated to prove the gain. Without the
before/after numbers this is guesswork, and the honest version of this
document says so.
