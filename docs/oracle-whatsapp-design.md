# Oracle on WhatsApp — design

Status: draft for discussion, 2026-09-25. Nothing here is built. Builds on
[oracle-design.md](oracle-design.md) (nudges, Ask Oracle) and the approval
path in `core/approvals.py`.

## The ask

The owner wants two things from Oracle:

1. When an agent needs input or approval and the owner is away, Oracle sends a
   WhatsApp message, and the owner answers there. The answer reaches the agent.
2. Small things do not reach the phone at all. Oracle moves agents forward on
   its own, using what it has learned about how the owner works. Only
   decisions that matter go to WhatsApp.

## The invariant: the model may escalate, never permit

Everything Oracle does on the owner's behalf comes from a **rule the owner
confirmed**. Model judgment can only route a decision *to* the owner, never
make one for them.

The reason is untrusted input. Oracle decides "is this small?" by reading an
agent's screen, its question, and the command it wants to run. A
prompt-injected agent writes that same text. If the model can approve, an
injection can approve. If the model can only escalate, the worst an injection
does is bother the owner.

This matches the existing invariants: the PM routine's "proposal ≠ execution"
([pm-routines-design.md](pm-routines-design.md)) and the inbox design's "no
auto-answering" ([inbox-awareness-design.md](inbox-awareness-design.md)).
"Learning the working style" therefore means **proposing rules from evidence**,
which the owner confirms once. It does not mean an LLM approving things at run
time.

## What "needs the owner" means

| Source | How duckterm sees it today | How it is answered today |
| --- | --- | --- |
| Permission request, Claude Code or Copilot | Blocking hook registers it in the approval registry and long-polls for 180 s (`_BLOCKING_TIMEOUT` 200 s) | Dashboard Approve/Deny, else the agent's own terminal prompt after the hook gives up |
| Permission request, Codex | Observe-only registry row from the `PermissionRequest` event | Keystrokes in the terminal |
| Agent asked the owner a question | Claude `Notification` with a non-idle type makes the session `waiting`; a turn that ends on a question is visible only in the transcript | Typing a reply in the terminal |
| Unread inbox mail | Session API | Oracle nudges already cover it |

Not owner-needed: Claude's idle notice, prompt suggestions, Codex
placeholders. The 2026-09-23/25 fixes already separate these.

Gap: a turn that *ends* on a question looks like `idle`. Detecting it needs
the last assistant message from the transcript (already read for Messages and
digests), plus a check that it asks the owner something. This is the only
place a model helps with detection, and it can only escalate.

## Three tiers

| Tier | Examples | Who acts | Channel |
| --- | --- | --- | --- |
| **Auto** | Matches a confirmed rule: approve `pytest -q` in a duckterm worktree; answer "yes, continue" when an agent asks whether to keep going on its stated goal | Oracle | None; logged, listed in the daily digest |
| **Ask** | Any other blocking permission or question | Owner | WhatsApp when away, dashboard when present |
| **Dashboard only** | Irreversible or outward actions: `git push --force`, deleting outside a worktree, releases and publishing, credential and connector changes, deleting sessions | Owner, at the machine | WhatsApp says "needs you at the dashboard" and offers no Approve button |

Tier assignment runs deterministic rules first: tool, command pattern,
folder, runtime. The model may then move an item Auto → Ask or Ask →
Dashboard only. It may never move one the other way.

Auto only covers actions that can be undone or that change nothing outside a
worktree. Oracle cannot undo a command that already ran, so irreversible
actions never qualify, however consistent the history.

## Learning the working style

### The data

| Signal | Where it lives today | Gap |
| --- | --- | --- |
| Approve/deny decisions | In memory, lost on restart (roadmap item 10) | Must persist |
| Owner's answers to agent questions | `UserPromptSubmit` after a waiting or question state | Needs linking to the question |
| Follow-up corrections and annotations | Events, annotations (AGENTS.md suggest) | Already used |
| Collaboration patterns | Digest `user_learnings` | Prose; useful as context only |

### The decision log

A new table, `oracle_decisions`: `id, at, session_key, folder, runtime, kind`
(`permission | question | nudge`), `tool, detail, question_text`,
`decided_by` (`owner-dashboard | owner-terminal | owner-whatsapp | rule:<id> |
timeout`), `decision, latency_ms, rule_id`. Every channel writes it, whether
the decision came from the dashboard, the terminal, WhatsApp, or a rule.

This is a schema bump. Per the schema-version gotcha, a feature-branch server
that migrates `db.sqlite` bricks older builds (`SchemaTooNewError`). Test
the migration only on a separate `DUCKTERM_INSTANCE`.

### From decisions to rules

1. A proposal pass runs when 20 new owner decisions have accumulated, and at
   most daily. One summarizer call groups decisions into candidate rules and
   attaches evidence, for example: "approved `pytest -q` 23 of 23 times in
   Duckterm worktrees, median 4 s".
2. A candidate needs at least 10 consistent owner decisions and no denials in
   its class.
3. **Shadow mode.** A confirmed rule first runs in shadow. It logs what it
   would have done beside what the owner actually did. It goes live after 10
   consecutive agreements. One disagreement sends it back to proposal.
4. The owner confirms or revokes each rule individually, from the dashboard
   or by WhatsApp ("stop R3").

Rules are data, not prompts: `{match: {tool, command_regex, folder, runtime,
kind}, action, evidence: [decision ids], confirmed_at, live_at}`. Applying a
rule is a regex match and needs no model call.

### Visibility

Each Auto action is an event in the session's history. A daily WhatsApp
digest lists them, for example: "Oracle handled 14 things today: 11 test
runs approved (R1), 3 'continue' answers (R2)."

## WhatsApp transport

| Option | Receiving replies | Cost | Verdict |
| --- | --- | --- | --- |
| **Twilio WhatsApp API** | **Polling:** `GET /Messages` lists inbound messages, so no public endpoint is needed | $0.005 per message in or out, plus Meta's template fees | **Recommended** |
| Meta Cloud API, direct | Webhooks only: public HTTPS, `X-Hub-Signature-256` HMAC. Needs a relay or tunnel (a stable Tailscale Funnel URL, or the planned share relay) | Meta fees only | Revisit if the share relay ships or volume grows |
| Unofficial WhatsApp Web clients (Baileys, whatsapp-web.js) | Outbound socket, no endpoint | Free | **Rejected.** WhatsApp's terms forbid unofficial automated clients, and bans hit the owner's personal account |

Why Twilio: the server binds loopback by design. Polling an API over
outbound HTTPS keeps it that way. There is no relay to run, no tunnel to
supervise, and no inbound surface to secure. Poll every 5 s while an
escalation is open, and every 60 s otherwise, so "status" or "away" commands
still arrive.

Constraints that apply either way (Meta rules, which Twilio passes through):

- **Separate sender number.** A number already registered on WhatsApp can't
  be a business sender. The owner's personal number is the recipient.
- **24-hour window.** Free-form messages are allowed only within 24 h of the
  owner's last message. Outside it, Oracle must open with a pre-approved
  **utility template**, for example "{{1}} needs your input: {{2}}" with
  quick-reply buttons (at most 3, 20 characters each). Template review takes
  up to 24 h. After the owner replies, the window is open again.
- **Pricing changes on 2026-10-01.** Service and utility replies become
  charged per message. Expected volume is tens of messages a day; confirm
  rates before phase 1.
- **AI-chatbot policy.** Meta's terms from 2026-01-15 bar general-purpose AI
  assistants. A fixed-purpose approval and notification bot is outside that
  reading, but free-form "ask Oracle anything" over WhatsApp could fall
  inside it. v1 keeps a fixed command set; Ask Oracle stays in the dashboard.
- **Identity.** Accept inbound messages only from the owner's address
  (Twilio `From`; with Meta direct, the business-scoped `user_id`). Drop and
  log everything else.

## Messages

Permission request:

```
main-qa (Duckterm · Codex) wants to run:
git push origin release-0.4.51
[Approve] [Deny] [Later]            ref A7
```

Question at the end of a turn:

```
architect (Duckterm) asks:
"Should I spec item 1 or item 2 first?"
Reply with your answer.             ref Q3
```

Dashboard-only item:

```
main-dev (Duckterm) wants to publish a GitHub release.
That needs you at the dashboard.    ref D2
```

- **Batching.** Escalations within 2 minutes go in one message as numbered
  items. Replies look like "1 approve, 2 deny".
- **Content.** A body holds at most 1024 characters, and long commands are
  truncated with the full text kept in the dashboard. A redaction pass
  removes token-shaped strings, reusing the connector secret patterns. Each
  folder can opt out of sending command text and send only "X needs you".
- **Refs.** Each ref is a short random handle mapped to one open item.
  Button payloads carry the ref, and a quoted reply (WhatsApp reply-to)
  resolves to it. Free text without a ref applies only when exactly one item
  is open; otherwise Oracle asks which one.

## Routing an answer back to the agent

| Case | Route |
| --- | --- |
| Blocking permission, hook still polling | `ApprovalRegistry.set_decision`, today's dashboard path |
| Hook gave up after 180 s, so the agent shows its own prompt | Send the harness's approve/deny keys, **only if** the screen still shows that prompt for the same command. Otherwise reply "that request is gone" |
| Codex observe-only permission | New `Harness.approval_keys(decision)`, gated on the same screen check |
| Question at the end of a turn | Paste the owner's text as a new prompt: "Reply from the owner via WhatsApp: …". It goes through Oracle's empty-prompt gate, and a draft blocks it with a reply saying so |
| Item already resolved elsewhere | Reply "already handled at the dashboard 2 min ago" |

Deduplicate on the provider's message ID, since both providers retry
deliveries. Each ref expires when the agent moves on: a new turn, the
approval clearing, or the session stopping.

The 180 s hook cap stays as it is. Holding a hook for hours would freeze the
agent's turn. The screen-verified keystroke path covers late answers.

## When to send

Send only when the owner is **away**. Otherwise desktop notifications work
as they do today.

- **Automatic away:** no dashboard keystroke or WebSocket input for 10
  minutes, and macOS HID idle time of at least 5 minutes (`ioreg -c
  IOHIDSystem`, stdlib subprocess).
- **Explicit:** "away" and "back" from WhatsApp, and a dashboard toggle.
- **Limits:** at most one message every 2 minutes (batched), quiet hours set
  by the owner, and a daily cap. Past the cap, one final message says "N more
  waiting, see the dashboard".

Known limit: if the Mac sleeps, nothing sends, and local agents stop too. The
remote workspace VM on the roadmap is what removes that.

## WhatsApp commands (fixed grammar)

`approve A7`, `deny A7`, a free-text answer to a question ref, `status` (one
line per waiting session, fixed format), `away`, `back`, `pause oracle`,
`resume oracle`, `rules` (list live rules), `stop R3`. Anything else gets the
command list back. Free-form Ask Oracle is excluded in v1 (see the
AI-chatbot policy above).

## Security

- **The phone becomes an approval channel.** Anyone holding the unlocked
  phone can approve Ask-tier items. That is why irreversible actions are
  Dashboard only. An optional PIN ("approve A7 4821") covers owners who share
  a device. The PIN has to be something the owner knows; a code in the
  message would not help.
- **Credentials:** the Twilio SID and auth token (or Meta system-user token)
  live in the macOS Keychain through the existing connector secret store,
  never in config files.
- **Data leaving the machine:** session names, commands, and agent questions
  go to Twilio and Meta. Redaction and per-folder opt-out apply (see
  Messages).
- **Reply text is the owner's authority, and nothing more.** It is pasted to
  one agent as that agent's prompt. Oracle never parses it as instructions
  for itself beyond the command grammar.
- **Audit:** every outbound message, inbound message, and resulting action
  is written to `oracle_decisions` with the provider's message ID.

## Failure modes

| Failure | Behavior |
| --- | --- |
| Provider outage or 5xx | Back off, keep desktop notifications, mark items "not delivered" in the dashboard |
| Template rejected or window closed | Fall back to the approved template; if none is approved, send nothing and surface it in the dashboard |
| Duplicate or out-of-order replies | Deduplicate by message ID; a reply to a resolved ref says so |
| Server restarted with open refs | Refs persist in the decision log; approvals persist once roadmap item 10 lands |
| Wrong ref, or several open items | Ask which, and never guess |

## Phases, each with its trigger

0. **No WhatsApp yet (needed regardless).**
   - Persist approvals (roadmap item 10) and the decision log.
   - Add a "needs you" detector, including turns that end on a question.
   - Add an **Oracle status view** that shows, per session, what Oracle last
     did and which gate held it back. That view would have answered the
     2026-09-25 "why isn't main-qa nudged?" question at a glance.
   - Show rule candidates in shadow mode.
   - Proceed when the decision log has two weeks of data.
1. **One-way alerts.**
   - WhatsApp messages with refs; the owner answers at the dashboard.
   - Proves usefulness, volume, and cost.
   - Proceed if the owner answers at least one alert from their phone per day,
     and wishes they could reply.
2. **Two-way.**
   - Approve, deny, and answers by ref, with Twilio polling.
   - Proceed when replies work reliably for a week.
3. **Auto tier.**
   - Confirmed rules go live after shadow agreement.
   - Grow the rule set only from evidence.
4. **Maybe.** A richer command set, or Meta direct if the share relay exists.

## Open questions for the owner

1. **Provider.** Twilio, which polls and needs no public endpoint for $0.005
   a message, or Meta direct, which is cheaper but needs a relay or tunnel?
   Recommendation: Twilio.
2. **Sender number.** WhatsApp needs a separate number for the sender. Buy
   one through Twilio, or use a spare?
3. **Dashboard-only list.** Is the list above right? What else must never be
   approved from a phone?
4. **Data sharing.** Is sending command text and agent questions to Twilio
   and Meta acceptable for every folder, or should some folders send only
   "X needs you"?
5. **Away detection.** Automatic (idle time), explicit ("away"/"back"), or
   both?
