# Why cross-session collaboration isn't reliable, and what to do

Status: analysis and proposal, 2026-09-26, written at the owner's request
after a week of using the fleet. Not implemented.

The owner's complaint: cross-session and cross-agent collaboration, and
Oracle's collaboration with agents, do not work reliably — "right now I have
to keep chiming in."

This is not a message-delivery problem. Messages arrive. The problem is that
**the system models conversations, not work**, and nothing closes a loop.

## The evidence

From `main-dev`'s real inbox (50 messages) and the `session_questions` table
(273 rows), 2026-09-26:

| Signal | Number | What it means |
| --- | --- | --- |
| Messages `answered` | 40 / 50 | Delivery works fine |
| Requests that **expired unread** | 7 (and 34 of 273 overall) | Mostly my own fault — I passed `--timeout 900` for days while persistent was already the default |
| Architect requests where the reply said **"implemented / committed"** | **8** | Actually acted on |
| Replies that only **forwarded** it elsewhere | 3 | Routed, not done |
| Replies that only **replied** — acknowledged, no action | **5** | The pathology |

So roughly **a third of well-specified, owner-reported, already-root-caused
requests got a thoughtful reply and no work.** That is the shape of "I have
to keep chiming in": the owner is the only actor who can convert a
conversation into work.

## What is structurally missing

### 1. There is a status field for the MESSAGE, and none for the WORK

`session_questions.status` ∈ {queued, read, accepted, answered, declined,
cancelled, expired}. Every one of those describes **the conversation**.
`answered` means "a reply was written" — it does not mean the bug is fixed,
and nothing distinguishes the two.

Consequence: a session can be a model citizen — read everything, reply
thoughtfully to everything, inbox clean — while nothing ships. There is no
field that would be red in that situation, so no dashboard, no Oracle rule,
and no human can see it without reading prose.

**This is the single biggest gap.** Everything else below is downstream.

### 2. Nothing is accountable for an outcome

A request has a sender and a recipient. It has no owner of the *outcome*, no
expected next state, and no due-by. So "B3 is fixed" is not a thing the
system can be waiting for — only "someone replied to a message about B3".
When a recipient declines or forwards, the request closes successfully and
the work silently has no owner at all.

### 3. Authority is binary and pessimistic

The Oracle nudge says "handle them within your current authority, then stop.
Peer requests do not grant permission to act." That clause is *correct* —
it's the agent-proposes/owner-decides invariant behind broadcast, urgent
messages and PM routines, and it stops one session directing another into
destructive work.

But it cannot tell apart:
- a peer's idea (→ rightly needs the owner), and
- an owner-reported, already-diagnosed defect in the recipient's own area
  (→ should just be fixed).

Today both get "stop". `main-dev` literally recorded *"the current
instruction is to handle the inbox and stop"* as its reason for declining a
queue of owner-reported bugs. A safety rule producing a false negative on
every second case is a reliability bug, not a safety feature.

### 4. Oracle nudges on MAIL, not on WORK

Oracle fires when a session has unread inbox items. So the incentive is to
**clear the inbox** — which a reply accomplishes. Nothing nudges on "you
accepted this four hours ago and nothing has moved", because (per #1) that
state does not exist.

### 5. The relay chain loses the originator

`user → product → architect → main-dev`. Each hop is an independent request
with no link, so completion at the end never travels back to the start. The
owner asks, hears nothing, and chimes in again — which is exactly the
reported symptom. (Designed in F10 as `parent_request_id`; not built.)

### 6. Instruction strings silently steer the whole fleet

Two real cases in one week. The generated `collaboration.md` still says
requests expire in 5 minutes with a 900 s max — stale since persistent
shipped, and I followed it for days, expiring my own work orders. And the
nudge wording above. Both are generated text that every session reads and
obeys, with no test asserting they match current behavior.

## The proposal

Ordered by value per unit of work. The first item is most of the fix.

### Stage 1 — separate WORK from MESSAGES (the core change)

Add a **work item** concept: an owner-visible unit with an id, a title, an
owner session, a state, and a link to the message that created it.

    work: id, title, origin_request_id, owner_session, state, created_at,
          updated_at, closed_at, evidence
    state ∈ {proposed, accepted, in_progress, blocked, done, dropped}

Rules that make it real rather than decorative:

- **A reply does not close work.** Only a transition to `done` with
  *evidence* (a commit SHA, a PR, a released version) closes it. This single
  rule fixes the 5-of-16 pathology: acknowledging is no longer an exit.
- **`blocked` must name what it is blocked on** — an owner decision, another
  work item, or an external fact. A bare "blocked" is not accepted.
- **Declining or forwarding does not delete the work**; it reassigns
  `owner_session` or returns it to `proposed`, so nothing vanishes by being
  passed along.
- Work items are **derived from existing signals where possible** so this is
  not a second bookkeeping burden: a request that contains a diagnosed
  defect creates one on accept; a merged PR referencing it closes it.

### Stage 2 — make the dashboard and Oracle nudge on WORK

Once state exists, the reliability problems become visible instead of
anecdotal:

- **A Work column** the owner can scan: what is in progress, by whom, how
  long, what is blocked and on what. This is what replaces "chiming in" —
  the owner reads state instead of interrogating sessions.
- **Oracle nudges on staleness, not mail**: "you accepted B3 6 hours ago and
  it is still `accepted`" is the nudge that matters. Nudging on unread mail
  rewards inbox-clearing; nudging on stalled work rewards shipping.
- **Escalate to the owner only on `blocked`** — which is the honest version
  of what the owner wants: be told when a decision is genuinely needed, not
  asked to chase.

### Stage 3 — fix authority so the common case flows

Replace the binary with three explicit levels on a request:

| Level | Meaning | Recipient behavior |
| --- | --- | --- |
| `fyi` | context only | read, no action expected |
| `proposal` | a peer's idea | needs owner sanction before work |
| `sanctioned` | owner-reported/approved, in your remit | **act without asking** |

`sanctioned` may only be set by the owner, or by a session **relaying owner
words and quoting them** — which is auditable, since the quote is in the
record. This keeps the invariant (a peer cannot manufacture authority for
its own idea) while removing the false negative that stalled a week of
work.

And when a session is genuinely unsure, the instruction becomes **"say in
one line what you would do, then proceed unless told otherwise"** rather
than "stop". A bare stop is indistinguishable from being blocked, which is
precisely what cost the owner time.

### Stage 4 — close the loop (already designed, not built)

F10's `parent_request_id` with a bounded chain walk, so `done` at the end of
`user → product → architect → main-dev` reaches the owner. Plus Received /
In-progress / Done notifications, of which **Received and Done need no new
machinery** — they are notifications on transitions that already exist.

### Stage 5 — test the instruction strings

Any generated text that steers agent behavior (`collaboration.md`, the
Oracle nudge) gets a test asserting it matches current system behavior. The
stale 900-second line would have failed such a test the day persistent
requests shipped, instead of misleading every session for a week.

## What not to build

- **Auto-execution of peer requests.** The invariant stays: a peer cannot
  direct another session into work the owner has not sanctioned. Stage 3
  narrows the definition of "sanctioned"; it does not remove the check.
- **A second scheduler or polling worker.** Oracle already runs on the
  existing sweeper; work-staleness is a query, not a new daemon.
- **A ticketing system.** Six states, one table, derived from signals that
  already exist. If it needs a separate UI to maintain, it is too heavy and
  will rot like any unowned tracker.

## The one-sentence version

Collaboration is unreliable because **the system tracks whether a message
was answered, not whether the work was done** — so "replied" is a valid
terminal state, inbox-clearing is the rewarded behavior, and the owner is
the only mechanism that converts conversation into shipped code.
