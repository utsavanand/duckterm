# Focus (pinned sessions) and request status updates — design

Status: designed 2026-09-26 at the owner's request (relayed by `product`).
Feature 1 is implemented on `feature/session-focus`, with owner-reviewed UI.
Release validation is pending. Feature 2 remains a design.

Focus uses owner-authenticated `PUT /sessions/:key/focus-pin` with
`{"pinned": true|false}`. Missing sessions return 404, invalid input 400, and
a fourth pin 409 (`Unpin one first`). `GET /sessions` includes `pinned` (0/1).
Schema v4 adds the column; the count check and write are one SQL statement.
Pins survive lifecycle changes; deletion frees the slot.

Both Focus and folder grids persist layout, separately under `rd.grid.focus`
and `rd.grid.folder.<folder>`. Invalid saved layouts fall back to a usable
default. Stopped and archived pins remain visible with their state instead
of opening a nonexistent terminal. No new polling loop is added.

---

# Feature 1 — Focus: pinned sessions view

## The reuse claim is correct, with one caveat worth knowing

`product` proposed reusing `GridView.tsx` + `gridLayout.ts`, fed by the
pinned set instead of a folder. **Confirmed.** `GridView` takes `title`,
`agents`, `folders`, `themeFor`, `onSwitchFolder`, `onClose` — it is already
"render these sessions as a draggable grid", and the folder is only the
*source* of the list plus a switcher label. Focus passes the pinned sessions
and a "Focus" title. Drag-to-split, resize, the dock, and `evenRow()` all
come free.

**The caveat:** layout does **not** persist today. `GridView` holds its tree
in plain `useState` seeded from `evenRow(agents.slice(0, 3))`, so today's
folder grid forgets its arrangement on every reload. The request asks for
"pins and layout persist across reload and restart" — so persisting layout
is **new work**, not inherited, and it applies to folder grids too. Decide
deliberately: persist Focus's layout only, or fix it for both. Recommend
both, keyed separately, since a user who rearranges a folder grid today and
loses it on reload is hitting the same disappointment.

## Where pin state lives: the server

The question was server vs browser. **Server.** Reasons, in order:

1. The request says "persist across reload **and restart**". localStorage
   survives reload but is per-browser — the Mac app's WKWebView and a
   regular browser tab would disagree about what is pinned, which is exactly
   the kind of split-brain that made the desktop-notification setting feel
   broken (B6).
2. Pinning is a property of the *session*, like its folder or theme
   override, not of the viewing device.
3. There is a precedent to copy: `folders` are stored server-side precisely
   so an empty one survives, and sessions already carry `grp`.

Shape: a `pinned INTEGER` (or `pinned_at`) column on `sessions`, an
owner-token route to toggle it, and the value rides the existing session
payload the dashboard already receives — no new polling. Cap enforced
**server-side** at 3: the 4th toggle returns 409 with "Unpin one first",
and the UI shows that rather than guessing. Never auto-swap; the owner was
explicit.

Layout persistence, separately: this is view state, not session state.
localStorage under an `rd.`-prefixed key (matching `rd.oracleOpen`,
termThemes, themeOverrides) is right for it — it is genuinely per-device,
and losing it costs nothing.

## UI

- **Pin toggle** on any session row and in the detail panel, in any folder.
- **Focus button** in the header beside Ask Oracle; disabled with a tooltip
  when nothing is pinned.
- Opens `GridView` with the pinned set, title "Focus", up to 3 columns by
  default via `evenRow`, drag to stack/rearrange as the folder grid allows.
- Terminals are live and typeable — free, since it is the same component and
  the same mounted PTY terminals.

## Edge cases to handle explicitly

- **A pinned session is stopped, archived, or deleted.** Unpin on delete;
  keep the pin but show the at-rest state for stopped/archived (the grid
  already renders non-live sessions). Do not silently drop a pin.
- **Fewer than 3 pinned** — grid renders 1 or 2 panes, no empty cells.
- **A pinned session is in a collapsed folder** — pinning is orthogonal to
  folder collapse; Focus ignores folder state entirely.

## Tests

Pin survives reload and server restart; the 4th pin is refused with the
exact message and no auto-swap; Focus is disabled with zero pins; deleting a
pinned session removes the pin; layout arrangement survives reload;
terminals in Focus accept input.

---

# Feature 2 — Request status updates

## The problem, restated precisely

A sender sees `queued → accepted → answered` only by polling
`duckterm session get`. Nothing tells them. I have felt this repeatedly:
I have asked `main-dev` the same backup question four times because I could
not tell whether it was seen, queued, or ignored.

## The three updates, and what each one actually costs

| Update | Trigger | Cost |
| --- | --- | --- |
| **Received** | recipient calls `accept` | free — the transition already exists |
| **In progress** | hourly while accepted and unanswered | needs a timer + a way for the recipient to say something |
| **Done** | recipient calls `reply` | free — the transition already exists |

So **Received and Done need no new machinery** — they are notifications on
transitions that already happen. Ship those first; they remove most of the
blindness. "In progress" is the only part that needs new moving parts.

## How the hourly update avoids interrupting an agent mid-turn

This is the question that matters, and the answer already exists in
[inbox-awareness-design.md](inbox-awareness-design.md): **never inject
mid-turn; surface at a turn boundary.** The task-end hook already fires at a
pause and already carries a notice for accepted-but-unanswered work.

So the hourly progress reminder is **not a new interrupt mechanism** — it is
an extra line on the notice the turn-end hook already emits, gated to
once per hour per request. Concretely:

- The server marks a request "progress due" when it has been accepted for
  ≥1 hour with no progress note since.
- The next time that recipient's turn ends, the existing notice includes
  "Request q-… has been accepted for 2h with no update — post one with
  `duckterm session progress q-… '…'`".
- If the recipient never posts one, **the sender still gets an update**:
  "still accepted, no update from the recipient". That is the honest
  version — the sender learns the state of the *request*, and separately
  whether the recipient said anything.

Crucially: a busy agent is never interrupted, and a session with no turn
boundary (long single turn) simply produces the "no update" variant. The
sender is never left guessing either way.

## Is `duckterm session progress REQUEST_ID "..."` the right shape?

**Yes**, and it should be a distinct verb rather than overloading `reply`.
The request already establishes the semantics: *replies mean finished work,
not "on it"*. Keeping progress separate preserves that — a progress note
must not close the request or satisfy the answer, and the status must stay
`accepted`. It also mirrors the existing verb set (`accept`, `reply`,
`decline`, `cancel`), so there is nothing new to learn.

Constraints: short (a one-liner, cap it — 500 bytes is plenty), does not
count against the sender's rate limits or pending caps, and multiple
progress notes accumulate rather than overwrite, so the sender sees history.

## Relayed chains: how "done" travels back

The chain is `user → product → architect → main-dev`. Today each hop is an
independent request with no link, so `product` cannot learn that main-dev
finished.

**Add an optional `parent_request_id` on ask.** When a request is created
while handling another, the creator passes the id it is working on. Then:

- "Done" on a child notifies the child's sender (normal behavior) **and**
  walks the parent chain to notify each ancestor's sender.
- The chain must be **explicitly passed, not inferred**. Do not guess a
  parent from "the request this session most recently accepted" — sessions
  handle several things at once and a wrong link sends a completion to the
  wrong human.
- **Bound the walk** (say 5 hops) and tolerate broken links: an expired,
  cancelled, or swept ancestor ends the walk quietly rather than erroring.
- Ancestors get a distinct, quieter notice than direct senders: "a
  downstream request you originated has completed", not a duplicate of the
  child's answer text — the answer belongs to the direct sender.

## What this is not

- Not a read receipt. "Received" means the recipient *accepted*, a
  deliberate act. Merely reading the inbox must not send anything — that
  property is already established in the session API and should hold.
- Not a nag mechanism. One update per hour maximum, at turn boundaries only.
- Not automatic progress *generation*. If the recipient says nothing, the
  sender is told exactly that. Never synthesize a status on the recipient's
  behalf.

## Tests

`accept` notifies the sender exactly once; `reply` notifies once and closes;
a request accepted for >1h surfaces the reminder at the recipient's next
turn end and not mid-turn; a recipient who posts nothing still produces
"still accepted, no update" to the sender; a progress note leaves status
`accepted` and does not satisfy the answer; multiple progress notes
accumulate; a child's completion notifies ancestors via
`parent_request_id`; a broken or expired ancestor ends the walk without
error; the walk is bounded; reading an inbox sends nothing.

## Sequencing

1. **Received + Done** — no new machinery, removes most of the blindness.
2. **`parent_request_id` + chain walk** — makes relayed work visible to the
   originator, which is the owner's actual complaint about chains.
3. **Hourly progress** — the only piece needing a timer; build it last, on
   top of the existing turn-end notice rather than as a new interrupt.
