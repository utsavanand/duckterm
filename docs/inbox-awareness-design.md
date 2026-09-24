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

## Urgent messages

An owner message can be marked **urgent**. What that does and does not mean
matters, because the word promises speed we cannot deliver.

**Urgent changes standing and persistence, not delivery speed.** Both normal
and urgent mail land in the inbox at the same instant and both surface at
the session's next turn boundary. Urgent inverts the three properties that
make normal mail easy to skip:

| | Normal | Urgent |
| --- | --- | --- |
| Notices while session is `waiting` | suppressed | yes |
| Re-notices | only on new arrivals | every turn until handled |
| Wording | passive ("you have 2 unread") | directive ("answer now, then resume") |
| Inbox ordering | by arrival | pinned to top, flagged |

The payload carries **anti-derail framing** so a quick answer does not
become a new project: the urgent text is wrapped as *"Answer this quickly,
then resume what you were doing. Do not restructure your work around it."*

Honest limits, which the UI must state rather than imply otherwise:

- It does not arrive sooner. A session 90 seconds into a tool call still
  answers at the end of that turn.
- It cannot force compliance. The notice is context the model reads;
  repetition makes it very hard to ignore, not impossible.
- Label it in the UI as "surfaces at the session's next pause and keeps
  reminding until handled" — not the bare word "urgent".

Trigger to revisit: if waiting for turn boundaries measurably costs minutes
in practice, reconsider — but the only mechanism that beats it is the
interrupt below, with its cost.

## Interrupt session (separate owner control)

True mid-turn interruption exists only as Ctrl-C to the agent, which
duckterm can already send. It **aborts the turn's in-progress work** — a
blunt and occasionally destructive instrument.

Expose it as an explicit, separately-named owner action ("Interrupt
session"), never as a side effect of marking a message urgent. The user
should always be choosing that tradeoff deliberately. Confirm before
sending, and state plainly that in-progress work is lost.

Why mid-turn message injection is NOT the answer: a busy agent is consuming
its own input stream; a paste arriving mid-turn lands in whatever input
state the TUI happens to be in — queued at best, corrupting a partially
typed line at worst (RETRO: the Shift+Enter class of bug). No message
framing fixes a transport that does not deliver mid-turn.

## Tests

- Turn end with an empty inbox produces an unchanged hook response (no
  notice, no extra work).
- Turn end with an owner broadcast pending produces exactly one notice line
  naming the count and the owner sender.
- A session in `waiting` state gets no notice.
- The same pending record does not generate a notice on every subsequent
  turn; a newly arrived one does.
- A harness without turn-end support is unaffected.
- An urgent item notices even when the session is `waiting`, and re-notices
  on the next turn if still unhandled; a normal item does neither.
- An urgent item's notice carries the directive wording and the
  anti-derail framing; handling it clears the repeat.
- Urgent items sort to the top of `duckterm session inbox` output.

## Not building

- Server-pushed injection into a busy terminal. Superseded for idle agents
  on 2026-09-23: Oracle pastes a fixed reminder into an idle agent's empty
  prompt behind draft-safety gates ([oracle-design.md](oracle-design.md)).
- Auto-answering or auto-accepting inbox items on the session's behalf.
- A polling worker per session; the hook fires on events that already exist.
