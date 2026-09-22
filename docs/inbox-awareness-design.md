# Inbox awareness — how a session notices it has mail

Status: backend implemented for Claude Code task-end notices, including unread
owner broadcasts and accepted peer assignments. Other runtimes remain unsupported.
Folder broadcast UI is delegated to UI-dev. See [delivery details](inbox-delivery.md).
Context: [session-api-design.md](session-api-design.md) and the folder
broadcast feature (owner messages fan out to every session's inbox).

## The problem

Sessions do not notice their inbox. The collaboration instructions say to
check it "when the user asks or at a suitable pause", and the design doc
**excludes** automatic terminal injection and auto-answering on purpose: an
idle session is not provably free (there may be draft text in its input),
and peer traffic should not interrupt work.

That tradeoff was written when inboxes carried only peer-to-peer questions.
Folder broadcast changes it: an **owner** message has different standing
from a peer's, and the owner has no way to make a session look.

## The invariant

A notice, never an instruction. Duckterm tells a session that mail exists;
the session decides whether and when to act. Nothing is injected into a
running turn, and nothing answers on the session's behalf.

## Stage 0 — the session polls itself (zero code, works today)

Any long-lived session can run Claude Code's `/loop` with:

> Run `duckterm session inbox`. Handle anything actionable; if the inbox is
> empty, stop without further work.

Per-session opt-in, no build. Weaknesses: only covers sessions deliberately
set up this way, and it spends a turn even when there is no mail. This is
also how the PM routine already works
([pm-routines-design.md](pm-routines-design.md)).

## Stage 1 — a turn-end notice from the hook (the real fix)

Fire at a **pause**, not mid-work: the agent's turn-end event (`Stop` for
Claude Code, the equivalent per harness). The pieces already exist —
`duckterm-hook.sh` already POSTs events and already reads responses back
from the server (the permission path blocks on a long poll), and
`/session-inbox-counts` already computes pending counts for the dashboard
badges.

- On turn end, the server checks that session's pending inbox. If empty,
  the response is unchanged and nothing happens — the common case costs one
  integer.
- If non-empty, the hook surfaces **one line** of additional context:
  `You have 2 unread inbox messages (1 from the owner). Run
  'duckterm session inbox' to read them.`
- **Suppress when the session is `waiting`.** A session that just asked the
  owner a question must not be nudged into going off to do inbox work
  instead; it is waiting on a human, and that takes precedence.
- Scope the notice to what deserves interruption: **owner broadcasts** and
  **accepted-but-unanswered questions**. A brand-new peer question can wait
  for the next natural check rather than generating a notice every turn.
- Repeat-suppression: do not re-notice the same records every single turn —
  notice on change (new arrivals since the last notice), so a session that
  chooses not to act is not nagged into a loop.

Harnesses without a turn-end hook keep Stage 0 behavior; the notice is a
per-harness capability on the `Harness` contract, defaulting to unsupported
(RETRO: per-harness behavior belongs on the contract, not behind
`isinstance`).

## Tests

- Turn end with an empty inbox produces an unchanged hook response (no
  notice, no extra work).
- Turn end with an owner broadcast pending produces exactly one notice line
  naming the count and the owner sender.
- A session in `waiting` state gets no notice.
- The same pending record does not generate a notice on every subsequent
  turn; a newly arrived one does.
- A harness without turn-end support is unaffected.

## Not building

- Server-pushed injection into a live terminal — recreates the problem the
  inbox-based broadcast design removed, and stays on the excluded list.
- Auto-answering or auto-accepting inbox items on the session's behalf.
- A polling worker per session; the hook fires on events that already exist.
