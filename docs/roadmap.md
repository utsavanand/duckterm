# DuckTerm — Roadmap

As of 2026-09-27, **v0.4.79** is released and installed. The sections below
are a dated log; **the status table directly below is the current truth**,
and [bugs-and-backlog.md](bugs-and-backlog.md) is the ordered working list.
Sources: TODO.md, RETRO.md, design docs, open PRs, and the peer sessions via
the session API.

## Status at a glance (reconciled 2026-09-27 against `main` f6575b3, installed v0.4.79)

| Item | Status |
| --- | --- |
| F3 Artifacts | Shipped v0.4.55; Mac preview fix (B8) v0.4.69; **full-window view shipped v0.4.75** (PR #98) |
| F9 Focus | Shipped v0.4.71 (PR #78) |
| F12 Work tracking | **Reverted in v0.4.74** (PR #96) per the owner's deferral; schema stays v5. Back on the roadmap for a lighter redesign. |
| F10 Request status updates | Removed with the F12 revert. Received/done updates and request chains are **not** on main. |
| B9 Terminal typing latency | **Shipped v0.4.78** (PR #95); native Mac confirmation still open (PR #112) |
| Local and remote sessions in one window | Shipped v0.4.75 (PR #68); location icons v0.4.76-0.4.77; remote clouds on the Oracle page v0.4.79 |
| B4, B7, B8 | Fixed (B4 verified on installed v0.4.73; B7 v0.4.70; B8 v0.4.69) |
| Control Tower | Shipped (PR #39); menu wording v0.4.73 |
| Inbox redesign | Shipped v0.4.68 |
| B3, B6, B10, F6, duck settle, waiting lifecycle | Open; see [bugs-and-backlog.md](bugs-and-backlog.md) |
| F11 Answer agents without typing | Stop-hook path now carries inbox reminders (`cd25224`); relaying owner answers not built |
| B2, F7, F8, Oracle on WhatsApp | Designed; waiting on owner review or scheduling |
| F14 Cross-host discovery and messaging | Designed ([cross-host-collaboration-design.md](cross-host-collaboration-design.md), PR #110) |
| Folder view + Feature tracker | Proposed: [folder-view-spec.md](folder-view-spec.md); needs rework now that F12 is reverted |
| F1, F2, F4, F5, urgent messages, Interrupt, approvals re-home | Not started |

Shipped since this doc was first written (2026-09-20 → 22):

- **Security + CI** (PR #3, `5a671d7`) and session collaboration — v0.4.27.
- **Backup CLI** — v0.4.34. `duckterm backup [--to PATH|gs://BUCKET]`:
  SQLite online backup, checkpoints/snapshots, Claude+Codex transcripts,
  credentials excluded, 0600 archive, GCS upload via the user's own gcloud;
  a failed upload retains the local archive. [backups.md](backups.md).
- **Q&A survives stop/resume** — v0.4.36, independently tested: queued and
  accepted requests survive credential rotation and late child exit events;
  explicit deadlines continue running. Default persistent assignments arrived
  in v0.4.38. Termination, archive, deletion, and scope loss remain final.
- **Folder sidebar dropdown + per-folder interaction history** — v0.4.37
  (ui-dev, `4745d02`).
- **Inbox persistence + task-end notices** — v0.4.38 (`5275197`):
  assignments default persistent, closed history retained 7 days, Claude
  accepted-work notices fire once and suppress waiting/approvals/loop
  recursion, no terminal writes.
- **Folder broadcast backend** — v0.4.39 (`80d94dc`): idempotent atomic
  fan-out to durable inboxes, `sender_kind=owner`, optional reply, read
  tracking, 7-day retention, excluded from peer quotas, owner-only auth.
  [folder-broadcast.md](folder-broadcast.md).

## Now (this week) — as of 2026-09-25

*2026-09-27: B4 is fixed; B3 is still open.*

1. **Fix the folder-broadcast scope regression (B3)** — reproduced again
   against installed v0.4.47; original report and regression sent to main-dev.
2. **Package the missing header icon/favicons (B4)** — independently found
   in v0.4.47; packaging and installed-asset validation need correction.

Branch protection and stop/resume are complete (see shipped items). PR #1 is
closed. Independent bookmark, backup, and menu acceptance results, including
open defects and test limits: [QA report](qa-2026-09-25.md).

## Oracle (owner-requested 2026-09-23)

Design: [oracle-design.md](oracle-design.md) and
[control-tower-design.md](control-tower-design.md). Shipped v0.4.45–v0.4.57:
idle-inbox nudges for Claude Code and Codex, on in every folder (kill switch
`DUCKTERM_ORACLE=off`); the control tower (the Oracle button: fleet insights,
agents as ducks by team, per-session messages, a full-height Ask Oracle chat).
Unreleased on main: Copilot nudges, and the settle and peer waits cut from 10
to 5 minutes. Copilot sessions get an inbox only by hand until item 9 lands.

Oracle Relay (needs-you notes in the chat, answers relayed to the session, rules
made in plain words) has shipped. Menu questions link to the agent's terminal
instead of being answered from the chat.

Next: **Oracle on WhatsApp**, design draft
[oracle-whatsapp-design.md](oracle-whatsapp-design.md), waiting on owner
answers to its five open questions. Its Phase 0 (a persistent decision log, a
"needs you" detector, and a status view showing why each session was or wasn't
nudged) is useful without WhatsApp and comes first. Later rules (stale state,
file collisions, scheduled AGENTS.md suggestions) are listed with triggers in
the design doc.

Shipped 2026-09-23–25 (v0.4.40 → v0.4.47):

- **Message folder UI + owner inbox labels** — v0.4.40 (`91c4ab2`).
- **Manual backup backend** — v0.4.41 (`a1e662d`): owner-only
  GET/PUT/POST `/backup`, remembered private destination, worker-thread job
  with progress, 409 on overlap, 400 on missing destination, local
  `archive_path` retained on GCS failure, restart recovery without
  automatic retry.
- **Terminal scroll-to-bottom + duck celebration + backup UI** — v0.4.42
  (`9218189`). Attach scrolls once after the first replay frame parses;
  wheel/pointer/navigation cancels it so ordinary output and resize never
  yank the user down. Ducks celebrate only on witnessed live transitions —
  seeds, SSE replay, and reload do not retrigger; reduced motion keeps the
  badge without the jump.
- **Completed-helper UI** — v0.4.43; **terminal-top regression fix** for
  hidden-but-mounted terminals — v0.4.44.
- **New / Settings menu grouping** — v0.4.45 (PR #6, `a2fa35a`): New
  groups session/folder; Settings groups theme, terminal colors, desktop
  notifications, backup, harnesses (AGENTS.md deliberately separate). Also
  fixed connector probes blocking the dashboard and attach/replay stealing
  menu focus.
- **Message bookmarks** — v0.4.46 (PRs #8/#10), refined in v0.4.47
  (PR #11): persistent per-message pins, exact-message navigation/highlight,
  saved-copy fallback, neutral icon-only strip with six-word hover previews
  and accessible names. Independent installed-package acceptance passed
  2026-09-25, including unsubmitted terminal-draft preservation.
- **Branch protection on `main`** — enabled 2026-09-24 (ruleset 23921834):
  PRs required, `python`/`web`/`browser` checks required, deletion and
  force-push blocked, **no bypass actors** (owner decision — sessions open
  PRs like anyone else; verified a direct push is rejected, not warned).
  Read back independently on 2026-09-25; PR #1 is already closed.
- **Backup cloud infrastructure** — dedicated GCP project
  `rubberterm-20260922`, private `gs://rubberterm-20260922-backups`
  (us-west1, uniform access, public-access prevention), upload/read/delete
  verified with non-sensitive data. No real backup uploaded yet.

## Shipped without a roadmap entry (recorded 2026-09-25)

Found by reconciling `main` against this document. Each is released and
documented; none was tracked here while it was being built. Listed so the
roadmap reflects the product, and as evidence for the process note below.

- **Message bookmarks** — v0.4.46/v0.4.47 (PRs #8, #10, #11). Pin/unpin a
  conversation message, compact strip above the terminal, click jumps to
  the exact message, saved-copy fallback if the transcript is rewritten,
  persists across restart. [message-pins.md](message-pins.md).
- **Message folder UI** — [message-folder-ui.md](message-folder-ui.md).
- **Inbox delivery semantics** — [inbox-delivery.md](inbox-delivery.md).
- **Hugging Face connector** — [hugging-face.md](hugging-face.md).
- **Shared connectors** — [shared-connectors.md](shared-connectors.md).
- **AGENTS.md template** — [agents-template.md](agents-template.md).
- **Installation guide** — [installation.md](installation.md).

**Process note.** The owner steers by this roadmap, so work that is
designed, built, and released without an entry is invisible when priorities
are set — bookmarks reached the architect session only after shipping. A
one-line heads-up when a feature *starts* is sufficient; no design review is
needed. Asked of `main-dev` and `ui-dev` 2026-09-24.

## Now (owner-prioritized 2026-09-25)

F6. **Show where comments were left in the Messages tab** (owner-requested
    and prioritized 2026-09-25; sent to `ui-dev`). Owner: "when I'm on the
    messages tab and I highlight and leave a comment, that text should be
    highlighted to show a comment was left there. Otherwise it's difficult
    to read where I left comments previously."
    Small, because the data already exists and is merely never read back:
    the `annotations` table already stores each note with its quoted text
    verbatim (history.py:122) and `GET /sessions/:key/annotations` is
    already routed (server.py:209); Messages.tsx only ever POSTs, so
    annotations are write-only in the UI today. Build: fetch alongside the
    transcript, refetch after submit, and mark quoted spans via a
    post-render DOM pass (replies render through `dangerouslySetInnerHTML`,
    so string-replacing the markup risks injecting inside a tag — the
    component already does a post-render walk for mermaid, so the pattern
    exists). Hover reveals the note; styling subtle and theme-aware.
    Edge cases that must be handled explicitly: a stored quote may no
    longer appear after a transcript rewrite (follow the bookmarks
    saved-copy precedent, and count unlocated comments rather than
    silently dropping them); short quotes match many times (highlight all,
    or document the choice); a quote spanning inline markup crosses DOM
    text nodes (degrade gracefully, never emit broken markup); quoted text
    is user input and must be escaped. Every previously-left comment
    lights up as soon as this ships.

## Designed 2026-09-26, awaiting owner preview approval

*2026-09-27: F9 **shipped in v0.4.71** (PR #78). F10's received/done updates
and request chains shipped inside F12 in v0.4.72; PR #96 would revert them.*

F9. **Focus — pinned sessions view** (owner-approved for design via
    `product`). Design:
    [focus-and-request-status-design.md](focus-and-request-status-design.md).
    Pin toggle on any session (max 3, 4th refused with "Unpin one first",
    never auto-swap), a Focus header button beside Ask Oracle opening the
    pinned set in the existing grid. **Reuse claim confirmed**: `GridView`
    already takes a session list plus a title, so drag-to-split, resize,
    dock, and `evenRow` come free. **But layout does not persist today** —
    GridView holds its tree in plain `useState`, so folder grids already
    forget their arrangement on reload; persisting it is new work that
    applies to both. Pin state goes **server-side** (a column on
    `sessions`, cap enforced server-side): it must survive restart, and a
    browser-local pin would make the Mac app and a browser tab disagree —
    the same split-brain that made the notification setting feel broken
    (B6). Layout is per-device view state and belongs in localStorage.

F10. **Request status updates** (owner-approved for design via `product`).
    Same design doc. Sender currently learns nothing without polling
    `duckterm session get` — I have asked `main-dev` the same backup
    question four times for exactly this reason.
    **Received and Done need no new machinery** — they are notifications on
    transitions that already exist; ship them first. The **hourly progress**
    update is the only new moving part, and it must not be a new interrupt:
    it rides the **existing turn-end notice**
    ([inbox-awareness-design.md](inbox-awareness-design.md)), gated to once
    per hour, so a busy agent is never interrupted mid-turn. If the
    recipient posts nothing, the sender still gets "still accepted, no
    update" — never synthesize a status on the recipient's behalf.
    `duckterm session progress REQUEST_ID "…"` is the right shape as a
    **distinct verb**, because replies mean finished work and a progress
    note must not close the request. Chains link via an **explicit**
    `parent_request_id` on ask — never inferred from recently-accepted work,
    since a wrong link sends a completion to the wrong human — with a
    bounded walk that tolerates expired ancestors.

## Shipped / in flight, recorded 2026-09-26

- **Artifact feedback** — shipped v0.4.57 (PR #44, plus ui-dev's PR #42
  Oracle layout): highlight HTML/Markdown/text in an artifact to comment and
  send it to the producing terminal; whole-file Feedback supports images;
  provenance carried; stale/stopped delivery errors retain the draft;
  isolated HTML selection bridge.
  **Note the asymmetry:** this is the annotate-a-selection interaction the
  owner asked for — but for *artifacts*. F6, the same idea in the **Messages
  tab**, is the owner's explicitly prioritized item and is still NOT built
  (verified 2026-09-26: `Messages.tsx` still only POSTs annotations; there is
  no highlight render path). Artifact feedback shipping first is worth
  knowing when judging priority order.


- **Independent left/right panel collapse** — shipped v0.4.56 (PRs #40/#41,
  `main-dev`): accessible header buttons, persisted choices, 36px reopen
  rails, center pane takes the freed width (verified 738 → 1035 → 1368 px),
  narrow stacked windows reclaim height.
- **Control Tower** — phases 1 and 2 implemented on branch `control-tower`
  (*since merged in PR #39 and shipped*): clicking Oracle opens a full page rather than a side panel,
  with fleet insights (agent count, total tokens, last backup, remote
  session count) and an animated duck scene grouped into teams by folder.
  Design: [control-tower-design.md](control-tower-design.md), built on
  [oracle-design.md](oracle-design.md). **Was not tracked here while being
  built** — recorded now; this is the second time a designed, implemented
  feature reached the roadmap only after the fact (see the process note
  above).

## Ownership, as reported by the sessions themselves (2026-09-25)

The owner asked whether items attributed to sessions were actually being
worked on. Asked directly; answers recorded as given rather than inferred:

- **`ui-dev` is AVAILABLE with nothing in progress**, and stated explicitly
  that annotation highlights (F6), packaged icons (B4), desktop
  notifications (B6), and the urgency/Interrupt controls are **pending /
  not started** — "please do not mark them in progress or completed".
  Priority order sent back: B6 → F6 → B4 → urgency/Interrupt.
- **`main-dev`** shipped Artifacts (v0.4.55) and the fork-race CI fix
  (v0.4.54); **B3 is explicitly still queued and not fixed by that
  release**. The backup sync-mode exclusion test remains unanswered after
  three asks.
- Lesson for this document: an item having a named owner is not the same as
  an item being worked on. Ownership lines here now mean "asked and
  confirmed", not "sent to".

## Now — owner-requested 2026-09-26

F11. **Answer an agent from Oracle's chat without typing into its terminal**
     (owner-requested via `main-dev`/Oracle). Today Oracle relays answers by
     typing into the agent's terminal — pasting replies and pressing digit
     keys in menus. Two failures on 2026-09-26: a two-question
     AskUserQuestion form where Oracle pressed `1`, the form advanced to the
     second question, and **nothing was submitted**; and a pasted nudge that
     **sat unsent for 14 hours** because Claude Code swallowed the Enter.
     Owner: "it shouldn't be that you have to type into the terminal."
     Interim shipped on `oracle-multi-question`: menu notes list every
     question with an "Open <name>'s terminal" button, and the chat no longer
     presses keys into menus.
     **Leads checked against the code (architect, 2026-09-26):**
     - **Lead 1 is real infrastructure.** `core/approvals.py` +
       `hooks/duckterm-hook.sh` already hold a hook open: the hook registers
       the request, long-polls `/approvals/:id/decision` for up to **180 s**,
       and returns a per-harness allow/deny shape. So "answer through the
       hook instead of the keyboard" is an extension of a working mechanism,
       not a new one. **But two concrete gaps:** the decision type is
       `Literal["approve", "deny"]` only — it carries **no payload**, so
       returning `updatedInput` with answers needs the registry, the HTTP
       route, and the hook's response shape all widened beyond a binary; and
       the **180 s cap** is far shorter than a human answering a
       multi-question form, so the timeout policy must change or the agent
       falls through to its own prompt mid-answer.
     - **Lead 2 (Stop hook returning `block` with a reason to continue the
       turn) is plausible but unverified** — it is a different hook event
       with different semantics from the permission path, so it needs a real
       probe against Claude Code before it is designed on.
     - **Lead 3 stands**: Codex and Copilot need their own equivalents or
       keep the typing path. Per-harness capability on the contract,
       default unsupported (RETRO rule), so "cannot answer without typing"
       is visible rather than silently degraded.
     **Design settled with `main-dev`, and their proposal is better than
     widening the decision type:** keep `Decision` as `approve`/`deny` and
     add an **optional answers payload alongside it**, emitting
     `updatedInput` only when answers are present. The three existing
     `set_decision` callers — auto-approval rules (server.py:2240), Oracle's
     relayed approval (2450), the dashboard route (3379) — then send exactly
     what they send today, so Approve/Deny carries no regression risk.
     Verified those are the only three callers and that no web or Mac client
     calls the decision route directly. F11 must add a test asserting the
     hook's output is **byte-identical when no answers are given**; existing
     guards are `tests/runtime/test_relay.py` plus the approvals tests.
     **Ownership:** the lead-2 Stop-hook probe is owned by the Oracle
     `main-dev` session (`0048fef0`) and **blocks lead 1's design**; the
     owner asked to ship the interim shortcut first, so it runs when the
     owner schedules F11. Per-harness capability flags on the harness
     contract (default unsupported) agreed. v0.4.64's submit confirmation is
     the first step toward making "accepted" distinguishable from "typed":
     UserPromptSubmit-confirmed versus prompt-stuck.
     **Lead 2 PROBE RESULT (Oracle `main-dev`, 2026-09-26): IT WORKS.**
     Tested against Claude Code 2.1.283, headless and interactive, on a
     private tmux server isolated from DuckTerm hooks. A Stop hook printing
     `{"decision":"block","reason":"…"}` continues the turn with that text;
     the transcript records it as a user message beginning "Stop hook
     feedback:". Guard the loop with `stop_hook_active` — the next Stop has
     it true, and the hook exits 0. Holding works: with a 300 s hook
     timeout it waited 25 s for an answer file, then blocked with it.
     **So free-text answers need none of lead 1's payload work** — the two
     cases split cleanly: structured menu answers via the permission path,
     free text via Stop. That is the scope halving I hoped the probe would
     settle.
     Four probe findings that shape the design, all of which must be
     honored: (1) if the owner types in the terminal while the hook holds,
     the message is **queued, not sent** — so the server must release the
     held hook as soon as the owner types in that pane; the supervisor
     already tracks owner keystrokes. (2) Esc interrupts the hook cleanly
     with no orphan process. (3) Claude's UI labels injected text "Stop
     hook **error**: <reason>" — cosmetic but misleading, so the reason
     string must say plainly that it is the owner's reply relayed by Oracle.
     (4) Holding on every Stop would put a spinner on every turn end, so
     hold only when an answer is actually expected.
     Still open from my side: the **180 s poll cap** on the permission path
     is far shorter than a human answering a multi-question form. The probe
     shows the Stop path can hold 300 s, so the two paths now have
     *different* timeouts — reconcile them deliberately, and make sure
     neither falls through to the agent's own prompt mid-answer, which is
     worse than today's failure.

## Now — collaboration reliability (owner-reported 2026-09-26)

**F12 shipped before the deferral registered, and is being reverted.**
Sequence worth recording, because it is a process finding rather than a bug:
the owner deferred F12 on 2026-09-27, but PR #84 had already merged and
released as **v0.4.72** — so deferred code reached the owner's machine.
`release-dev` acknowledged the missed hold and is shipping **PR #96**, an
owner-approved schema-preserving revert, before integrating B9's #95.
The revert is done correctly and I verified it rather than trusting the
summary: it removes `work_items.py`, the Oracle/session-API wiring and the
docs, but **keeps `user_version=5`, keeps the `pinned` column, drops no
data**, and adds a test asserting a v5 database opens after the revert with
its rows intact. That is the right shape — a deferral should not cost the
owner a migration or a downgrade path.
The lesson: a hold is only effective if it reaches the gatekeeper before the
release train does. Worth a standing rule — a deferral instruction should
name the PR number and go to `release-dev` directly, not only to the
implementing session.

F12. **Work tracking / Work UI — DEFERRED by the owner 2026-09-27** for
     design reconsideration, not active implementation. The owner's concern:
     it risks becoming a **heavyweight project-management interface**, and
     they want to think it through first. That is a fair objection and the
     design doc partly invited it — six states, evidence, blockers and
     handoffs is close to a ticketing system, which the doc itself warned
     against ("if it needs a separate UI to maintain, it is too heavy and
     will rot"). `release-dev` holds PR #84; `ui-dev` stopped the Work UI.
     **Preserve as separately delivered:** the lightweight inbox and Oracle
     improvements that shipped alongside it — persistent requests, the
     reworded nudge, nudge memory across restarts, the two-notice
     four-hour backoff — are real wins and are not part of the deferral.
     **The underlying problem does not go away with the mechanism**, so it
     stays recorded: `answered` still means "a reply was written", not
     "the bug is fixed"; of 16 measured requests, 5 got a reply and no
     work. If the heavyweight version is wrong, the lightweight question to
     answer on reconsideration is: what is the *smallest* thing that makes
     un-acted work visible without a tracker to maintain? One candidate
     worth considering — a single derived "replied but nothing shipped"
     signal computed from data already present (request answered, no
     commit/PR referencing it), with no new states for anyone to update by
     hand.
     Original analysis:
     [collaboration-reliability-design.md](collaboration-reliability-design.md).

Cross-session collaboration is unreliable; the owner has to keep
     chiming in (original F12 analysis, retained for context). **Owner-approved; assigned to the `main-dev` session in
     the `oracle` worktree** (2026-09-26) — it is free, and Stage 2 changes
     what Oracle nudges on, which is that session's own code. The main-repo
     `main-dev` is mid-build on F9 pinning and the owner's position is that
     a session already building should not change direction midway.
     **A live illustration of the bug, worth keeping:** F12 was
     owner-approved, the approval was relayed, and it was parked as a
     "follow-up design" because a direct terminal instruction outranked a
     relayed one. No priority order is held across sessions — F12 lost to
     the exact problem F12 fixes. Stage 3's `sanctioned` level (settable by
     the owner, or by a session quoting owner words) would have prevented
     it.
     **Coordination window, closing now:** Stage 2 wants a Work column, and
     `ui-dev` is redesigning the Inbox this week (preview at
     `docs/previews/inbox-clarity.html`, awaiting owner review). That is the
     surface where work state belongs — getting it into the redesign is
     cheaper than bolting it on afterwards. Analysis and proposal:
     [collaboration-reliability-design.md](collaboration-reliability-design.md).
     **Not a delivery problem — messages arrive.** Measured from
     `main-dev`'s real inbox (50 messages) and `session_questions` (273
     rows): of 16 architect requests with replies, **8 were acted on, 3 were
     forwarded, and 5 got a thoughtful reply and no work**. Plus 34 of 273
     requests expired unread (largely my own fault — I passed
     `--timeout 900` for days after persistent became the default).
     Root cause: **there is a status field for the MESSAGE and none for the
     WORK.** `session_questions.status` values all describe the
     conversation; `answered` means "a reply was written", not "the bug is
     fixed". So a session can read everything, reply well, keep a clean
     inbox, and ship nothing — and no field would be red. Downstream of
     that: nothing owns an outcome; Oracle nudges on unread *mail*, which
     rewards inbox-clearing rather than shipping; authority is binary, so
     an owner-reported diagnosed defect in a session's own area gets the
     same "stop" as a peer's speculative idea (`main-dev` recorded exactly
     that as its reason for declining a queue of owner-reported bugs); and
     relay chains lose the originator so completion never reaches the owner.
     Proposal, in value order: (1) a **work item** with states
     proposed/accepted/in_progress/blocked/done/dropped where **a reply
     does not close work — only `done` with evidence does**, and `blocked`
     must name what it is blocked on; (2) dashboard Work column plus Oracle
     nudging on **staleness rather than mail**, escalating to the owner only
     on `blocked`; (3) three authority levels — `fyi` / `proposal` /
     `sanctioned`, where `sanctioned` is settable only by the owner or by a
     session quoting owner words, and an unsure session says in one line
     what it would do **and proceeds** rather than stopping; (4) F10's
     `parent_request_id` chain walk; (5) tests asserting generated
     instruction strings match actual behavior — the stale 900-second line
     in `collaboration.md` would have failed one the day persistent shipped.

## Implemented, awaiting release (recorded 2026-09-27)

*Later on 2026-09-27: F9 shipped in v0.4.71 and F12 in v0.4.72. The owner had
deferred F12 before it shipped; revert PR #96 is open.*

Sessions asked that **implemented** be recorded separately from **installed**
— a fair distinction this document has been blurring. None of these is on the
owner's machine yet.

| Item | Branch / PR | Gate | Status |
| --- | --- | --- | --- |
| **F12 collaboration reliability** (backend/CLI) | PR #84, now `d66ccd1` | 793 Python / 110 UI / 63 browser | QA + release pending; `release-dev` integrates **after #78** |
| **F9 Focus + session pinning** | PR #78, `8bfed48` | 776 Python / 115 UI / 64 browser | QA + release pending |
| ~~Inbox redesign~~ | PR #80, `d1150aa` | 771 / 111 / 64 | **SHIPPED v0.4.68** (`62c7e37`) |
| ~~Context window fix~~ (B7) | PR #82 **merged** | 115 UI, tsc clean | ships in **v0.4.70** (PR #89), with a RETRO lesson |

**F12 was built by the main-repo `main-dev`, not the oracle-worktree one** —
correcting PR #81's assignment; the oracle session confirmed no duplicate
work. Implemented: durable work independent of replies, evidence/blockers/
handoffs, scoped request-chain updates, stale-work Oracle integration,
restart cooldowns. Remaining on F12: the Work UI preview with `ui-dev`,
verified GitHub merge closure, and blocked-work Oracle escalation.

**F12 self-correction worth recording** (`main-dev`, 2026-09-27): an Oracle
review of its own implementation caught **endless hourly work reminders** —
the staleness nudge would have fired forever on unchanged work. Now two
notices with a four-hour backoff, then escalation to the requester as
attention-needed. That is the right shape: nudging on staleness exists to
stop work being silently dropped, not to nag indefinitely, and an infinite
reminder would have recreated the alert fatigue that made mail-nudges easy
to ignore in the first place.

B8. ~~Artifact markdown previews render as a blank off-white box~~ **Fixed
    in v0.4.69** (PR #83): the Mac app's navigation filter refused the
    preview's `about:srcdoc` load; browsers were never affected. Original
    report:
    (`product`, 2026-09-27). Artifacts now *lists* correctly — 3 artifacts —
    so this is **not** the zero-artifacts empty-state hypothesis I proposed;
    that one is ruled out. Every markdown preview is blank. Details with
    `main-dev`; PR #83 (Mac app allowing `about:srcdoc` in subframes so
    artifact previews render) looks like the likely fix — worth confirming
    it covers the markdown case and not only HTML.

## Implemented, awaiting release (2026-09-27/28)

- **B9 terminal responsiveness** — PR #95, now `528072d`. A release
  blocker was found and fixed: the restored selection was **expanding
  sidebar folders on restart**, traced to `useSessionSelection`; selection
  now survives without reopening folders. Gate 137 frontend + 68 browser.
  **Caveat stated by `main-dev`, kept visible: native latency evidence is
  still inconclusive.** The browser p95 numbers (70.5 → 25.7 ms at 23
  terminals) are solid and independently reproduced, but the owner works in
  the **Mac app** — so the fix is proven in Chromium and unproven in
  WKWebView. That is the same class of gap RETRO records three times over
  (menus, clipboard, dialogs): browser e2e proves the page, not the shell.
  Confirm natively before calling B9 done. Earlier at `6028d8b`; independent QA
  reproduced ~25 ms p95 at 5/15/23 sessions; 15 browser + 3 unit tests;
  same PTY PID and draft intact after eviction. Native acceptance pending.
- **Full-window artifacts** (`ui-dev`) — `0c6436f`, owner approved the
  preview. Expand/title, full-window Back/Feedback/Download, scroll and
  focus return, Escape from iframe, browser Back, Markdown/HTML/image/text,
  mobile. Gate: 123 frontend + 66 browser; a separate native WKWebView
  actual-srcdoc bridge probe passed.
- **F12 revert** — PR #96, schema-preserving (keeps `user_version=5` and
  `pinned`, drops no data, with a test proving a v5 DB opens after revert).

**Tier 1 is still entirely unbuilt** — verified against `main` 2026-09-28,
not inferred: `Messages.tsx` has only the annotations **POST** and no GET or
highlight path (F6); `App.tsx` has no persistence for `notifyOn` (B6); no
adversarial scraper test exists; `pyproject.toml` still does not package the
favicons (B4). All four are owner-reported or owner-prioritized, all four
have an explicit owner go-ahead, and none is blocked on a preview or a
decision. F6 in particular is the owner's named priority and the highest
value-per-hour item on the board: every comment the owner has ever left is
already stored in the `annotations` table and invisible, so they all appear
the moment anything renders them.

## Installed 2026-09-28 — v0.4.75

Verified on the owner's machine (`duckterm --version` = 0.4.75):

- **Unified local/remote window** (PR #68, `feature-remote-session`): local
  "This Mac" rows and remote rows in one native window, with a remote Claude
  terminal selectable. Selected host/session **persists across relaunch,
  including remote-offline startup**. Verified against the live fleet: all
  23 local panes alive, the existing remote Claude PID survived, quit and
  relaunch regression passed.
- **Full-window artifacts** (`0c6436f`) and the **Context-panel reopen arrow
  fix** (`26e855c`, regression proven red first).
- **F12 revert** shipped in v0.4.74, schema-preserving.

Caveats the reporting session was careful to state, and worth keeping:
the remote service is still on **0.4.64** — the desktop coexistence fix
works against that version and it was deliberately not upgraded or
restarted; and **destination Git credential setup, reboot recovery, and
connector rotation validation remain unvalidated** — this completion makes
no claim about them.

F13. **Compact session-location indicators** (owner-requested 2026-09-28,
     `ui-dev`). "This Mac" text takes too much space; use an icon for local
     and a duck-in-cloud for remote. Preview prepared for visual review —
     the existing animated duck with a local computer badge, a remote
     cloud, and a disconnected slash, with the machine name on hover. No
     product changes pending the owner's approval.

F13. **Compact session-location indicators — IMPLEMENTED, awaiting
     QA/release** (owner-approved 2026-09-28, `ui-dev`, `a8031276`). Local
     rows use the existing animated duck with a computer badge; remote is a
     duck in a cloud, crossed when disconnected; host name on hover and
     keyboard focus with an accessible label. Full gate exit 0, 135
     frontend + 68 browser. It addressed the design risk raised in review:
     the duck's **activity animation is preserved**, so location and state
     do not collapse into one glyph, and the native outage check was
     updated to inspect the accessible icon status once status moved from
     row text into an icon. Evidence caveat: browser checks used a
     controlled remote bridge, **not live SSH**.

F14. **Cross-host session discovery and messaging** (owner-requested
     2026-09-28). Design:
     [cross-host-collaboration-design.md](cross-host-collaboration-design.md).
     Symptom: local `sotto` cannot discover `sotto-remote` although both
     appear in the unified Mac app.
     **Not a bug — a deliberately unbuilt capability.** `/peers` resolves
     both parties from the *local* SQLite and refuses unless roots match, so
     a session on another machine is invisible rather than denied; the Mac
     app is the only thing that spans hosts (via its SSH
     `session-request` proxy); and `hostTransport.ts` states outright that
     *"desktop grouping is presentation metadata… must not change remote
     inbox authorization"*. The job is to add the capability **without**
     converting "drawn in the same list" into "can message each other".
     Recommended architecture: **desktop-relayed, servers stay
     loopback-only** — the app already holds authenticated SSH transports,
     so nothing new becomes network-reachable and SSH keeps owning auth
     (same reasoning as connectors and `backup --to gs://`). Rejected:
     server-to-server federation, which would make every laptop an inbound
     network service against the whole security model. Deferred: relay-
     mediated, which is the only headless option and should be revisited
     when `share.duckterm.com` ships.
     Key constraints: host-qualified identity reusing the existing
     `~remote~<hex>` shape so equal session ids on two hosts stay distinct;
     **explicit owner-granted teams stored on BOTH servers** so either can
     revoke independently; store-and-forward with the **existing**
     idempotency keys so a retry after a half-delivered send is safe;
     unreachable peers shown as unreachable rather than absent; and a
     bare session id still resolving locally so no command changes meaning.
     **Owner approved the architecture 2026-09-28** — app-required is
     acceptable ("don't mind mac app open for local to remote messaging"),
     so desktop-relayed is settled and the relay stays a later headless
     path. Two smaller decisions remain in the doc: grant granularity
     (recommend folder-to-folder) and whether a remote peer is visible
     pre-grant (recommend invisible).

## B9 is the oldest unshipped fix (2026-09-28)

**The owner's typing-latency fix has still not reached them.** Installed is
**v0.4.77**; PR #95 is open and unmerged, and `main` has neither the bounded
terminal cache nor a memoized `Terminal` — verified, not inferred.

Meanwhile v0.4.76 and v0.4.77 shipped **on top of it**: compact
session-location icons, then the owner's correction to plain local ducks,
plus the folder-on-reload fix. Those are fine changes, but the sequencing
means an owner-reported performance bug — diagnosed, fixed, independently
QA'd at ~25 ms p95, and blocker-cleared — has been overtaken twice by newer
UI work.

This is the same pattern already recorded for Tier 1: **newer work with a
preview overtakes older work in a queue**, regardless of which the owner
cares about more. B9 differs only in that it is *finished* and merely
waiting, which makes it the cheapest possible thing to ship.

Recommendation, one line: **ship #95 next, ahead of further UI work**, and
confirm the fix natively in the Mac app rather than only in Chromium.

## B9 SHIPPED AND INSTALLED — v0.4.78 (2026-09-28)

The owner's typing-latency fix is **on their machine**. Verified rather than
inferred: PR #95 merged, `web/src/Terminal.tsx` exports a `memo(...)`
wrapper, `App.tsx` keeps only recently-visited terminals mounted, and the
installed bundle (`index-DFLDw0D3.js`) contains the memo call.

The merge conflict that had held it for four releases was resolved — the
`useSessionSelection.ts` overlap turned out to be, as suspected, the same
fix that had already shipped separately via PR #104.

Measured improvement (browser p95, independently reproduced): 5 terminals
26.7 → 25.9 ms, 15 terminals 51.2 → 26.0 ms, **23 terminals 70.5 → 25.7 ms**
— flat rather than merely lower, so adding sessions no longer degrades
typing.

**Still open: native confirmation.** All numbers are from Chromium and the
owner works in the Mac app. RETRO records three cases where browser e2e
passed while the WKWebView shell was broken. The acceptance test that
settles it is the simplest one — **the owner typing across ~23 sessions and
noticing it is no longer slow.**

B11. **Copy from a remote session's terminal — unresolved, untracked
     until now** (`ui-dev` flagged it 2026-09-28 as "remote-copy report
     remains unresolved pending affected session/view"). Recorded here so
     it stops living only in an inbox message.
     Why it is plausible rather than speculative: the Mac app's clipboard
     path is a bridge (`__rtCopy`/`__rtPaste` in `clipboardBridge.ts`),
     added because **xterm renders selection on canvas so WKWebView's
     responder-chain copy is inert** (RETRO 2026-09-20). Remote sessions
     reach the app through a *different* path again — `hostTransport`'s
     SSH `session-request` proxy. So "copy works locally" does not imply
     "copy works on a remote session"; they are two different routes to the
     same-looking UI.
     What is needed to act on it: the affected session and view. Whoever
     hit it should say whether it was a remote terminal in the unified
     window, which density, and whether ⌘C did nothing or copied the wrong
     thing — those point at different layers.

## Bugs — open

B9. **Terminal typing latency — FIXED, awaiting release** (PR #95,
    `6028d8b`). Measured before and after at 5 / 15 / 23 mounted terminals,
    p95 keystroke latency:

    | Terminals | Before | After |
    | --- | --- | --- |
    | 5 | 26.7 ms | 25.9 ms |
    | 15 | 51.2 ms | 26.0 ms |
    | 23 | **70.5 ms** | **25.7 ms** |

    Latency was scaling with session count — nearly tripling from 5 to 23.
    It is now **flat**: 2.7× better at the owner's working scale and no
    longer degrading as sessions are added. That confirms the diagnosis
    (hidden terminals parsing and reconciling), not data volume.
    Fix: bound the browser terminal cache to the 3 most recent views,
    memoize `Terminal`, stabilize the session list. 14 real-PTY browser
    tests including **drafts surviving eviction** — the right thing to
    guard, since an evicted terminal must not eat half-typed input.
    Deferred unless further profiling warrants: the timestamp clock,
    document-visibility polling, and server-side `/sessions` caching.
    Messages and Inbox already unmount hidden panels.
    (Owner-reported and selected 2026-09-27.) `main-dev` is on branch
    `fix/terminal-responsiveness` from latest main, working from
    [performance-investigation.md](performance-investigation.md) with
    before/after browser measurements. Initial focus: mounted terminal
    count and unnecessary React work; no new visible UI. (Owner-reported
    2026-09-27.) Investigation:
    [performance-investigation.md](performance-investigation.md).
    Measured: 23 live sessions, 44 artifacts, 30,286 events, DB 6 MB → 42 MB
    in a week. Data volume is not the problem; per-session work multiplied
    by session count is.
    **Main cause — every PTY terminal stays mounted always.** `App.tsx`
    renders all `ptyOwned` sessions and hides the unselected with
    `display: none`, deliberately, so switching does not reconnect the WS
    and replay the buffer. At 23 sessions that is 23 live xterm instances,
    23 WebSockets and 23 parsers decoding continuously — a busy hidden
    agent still parses every byte and holds 5,000 lines of scrollback, and
    the foreground terminal competes with 22 others for the main thread
    that also handles keystrokes. Matches the symptom exactly: monotonic in
    session count, worst when other agents are busy.
    Contributing: a **1 Hz whole-dashboard re-render** (`useNow(1000)`)
    that rebuilds `sessions` as new objects every second while `Terminal`
    is **not memoized**; **stacked polling** (approvals 2 s, Messages 3 s,
    inbox ~3 s, history 10 s, seed 30 s) that runs regardless of
    visibility; and **`/sessions` doing up to three filesystem touches per
    session per call** (`_transcript_stats_for` reads the transcript tail,
    `_suites_for` inspects the directory, `_reconcile_waiting` reads the
    tmux screen) — ~69 at 23 sessions, every 30 s.
    Fix order: cap mounted terminals to the selected plus N most-recent;
    memoize `Terminal` and stabilize `sessions` identity; scope the clock
    tick; make polling visibility-aware; cache the per-row `/sessions` work.
    **Measure keystroke-to-paint latency against mounted-terminal count
    first** — without before/after numbers this is guesswork.
    Not the fix: a Go/Rust byte-pump sidecar (the symptom is client-side, and
    a sidecar cannot help a main thread reconciling 23 React subtrees) or
    pruning the database.



B6. **Desktop notification setting does not work** (owner-reported
    2026-09-25, `ui-dev`). Three distinct defects in App.tsx:171-201:
    - **It does not persist.** `notifyOn` is plain `useState` initialized
      from `Notification.permission === "granted"`; nothing writes or reads
      localStorage. Turning it OFF reverts on reload, so "off" is
      effectively unachievable once permission is granted. Every
      neighbouring control in the same menu (density, theme, terminal
      themes, `rd.oracleOpen`) persists — notifications are the lone
      exception. Fix: store under an `rd.` key, initialize from stored AND
      current permission so a revoked permission beats a stored true.
    - **No feedback when permission is denied.** `requestPermission()`
      resolves "denied" immediately without prompting once a site is
      blocked, so the checkbox snaps back with no explanation — which reads
      exactly as "the setting doesn't work". Say "blocked in your browser"
      instead; HeaderMenus already has the `header-notification-help` slot.
    - **Burst on load and on enable.** `prevWaiting` starts empty and
      `notifyOn` is in the effect's dependency list, so already-waiting
      sessions all look new: they notify at load, and toggling the setting
      ON notifies for every currently-waiting session. Same class of
      mistake the duck celebration got right by firing only on witnessed
      live transitions. Fix: prime `prevWaiting` without notifying; don't
      treat a `notifyOn` change as a transition.
    Note: the Mac app has an independent native notifier
    (main.swift:14-57, its own `notified` set), so in DuckTerm.app the
    browser checkbox and the native path are two mechanisms — the Settings
    control should govern both or say that it only affects the browser.



B5. ~~Opening Oracle corrupts terminal wrapping irreversibly.~~ **Fixed by
    layout** in v0.4.51 (PRs #27/#28): Oracle now occupies the right
    context pane without narrowing the terminal, so the geometry that used
    to be lost is never disturbed. Regression test
    `web/e2e/oracle-terminal-resize.spec.ts` drives a real terminal and
    asserts both the reported size and the rendered rows survive
    open/close. Owner's bug no longer reproduces.
    **Latent defect retained, deliberately recorded:** the underlying cause
    is untouched — `settleOpening` in Terminal.tsx still early-returns
    `if (!visible || !host.clientWidth || !host.clientHeight)` *before*
    `fit.fit()`/`sendResize()`, and the ResizeObserver is wired to it, so a
    callback firing while the host is unmeasurable still drops a resize
    with nothing to retry. Not triggered by Oracle any more; still reachable
    from grid splits, folder-grid open/close, Messages/History tab
    switches, window resize, or any future pane. If terminal geometry goes
    wrong again, start here rather than re-deriving it. Cheap hardening
    whenever that function is next touched: schedule a retry instead of
    returning silently (RETRO precedent: the blank Mac window, where a
    failed load had no retry).

## In flight (2026-09-25)

- **Public website + custom domain** — **shipped**:
  https://duckterm.utsava.xyz/ (verified HTTP 200, title "DuckTerm — One
  place for your coding agents", no RubberTerm leakage). Demo uses the
  actual UI with fictional data. Source lives in the `rubber-duck` repo
  (PR #26), not this one — worth knowing when looking for it.
- **Sidebar density** — **shipped** v0.4.51: Compact / Standard / Relaxed
  in Settings with the choice remembered, distinct detail and action
  layouts, regular-weight session names. (A density row wrapper stealing
  terminal focus was caught by ui-dev's own browser suite before release.)

## Designed 2026-09-25, awaiting owner review

F7. **Plan hand-off** — plan with one agent/model, implement with another
    (owner wants it soon; Conductor has it). Design:
    [plan-handoff-design.md](plan-handoff-design.md). Decisions: same
    worktree (not a fork — fork is for parallel attempts and creates
    immediate divergence, wrong shape here); seed from the planner's last
    assistant message, user-editable (not Claude's plan-mode file, which
    would make the feature Claude-only); implementer recorded as a child of
    the planner. Not a conversation transfer — transcripts are
    harness-specific, so the implementer is *seeded*, not resumed. Needs a
    UI preview before build. Open question for the owner: expose a model
    field per harness at hand-off, or agent-choice only (recommended).

F8. **DuckCloud** — product name for the `remote-session` work plus the
    setup experience. Design: [duckcloud-design.md](duckcloud-design.md).
    Bring-your-own-cloud; **GCP and AWS both at launch** (owner decision).
    Sign-in rides the user's existing `gcloud`/`aws` CLI login — no OAuth
    app, no stored cloud credentials — the same reasoning as connectors and
    `backup --to gs://`. Setup replaces "have a project" with cloud /
    account / size, sizes quoted with hourly price, every resource named
    `duckterm-` so the user can clean up in their own console. Cost
    estimate before provisioning and a running estimate in the dashboard;
    **idle shutdown on by default**, with honest warnings that stopping a
    VM terminates its processes and that disk still bills while stopped.
    Does NOT reorder B2: `remote-session` already rewrote `connectors.py`
    (689 lines), so the connector wizard should be designed against that
    branch, making connectors and the merge one sequenced piece of work.
    **The branch is the long pole and the main risk** — it carries remote
    workspace, session migration, and the connector rewrite while five
    sessions push to main daily.

## Bugs (user-reported 2026-09-23, fix before new features)

B1. ~~Messages panel shows the previous session's transcript.~~ **Fixed**
    2026-09-24 (`5a40863`). Root cause: `<Messages>` rendered without a
    React key in App.tsx:403, so one instance was reused across switches
    and `messages`/`loaded` survived. Fixed at both ends (key + clear state
    before the first fetch). The regression test switches sessions WITHOUT
    a changing key — the genuinely broken path — and was verified to fail
    on the unfixed code.

B2. **Connectors are configured but not usable.** Reported: "I have
    multiple connectors but I don't know if I can really use them." Needs
    diagnosis before a fix: is the MCP server registered but not reaching
    the agent, is the state unclear in the UI (available vs configured vs
    connected — already on connectors-dev's list), or do connectors only
    reach local claude-code/codex and not remote/other harnesses (a known
    gap main-dev raised)? Assign diagnosis to connectors-dev.

B3. **Folder-rename scope bug, still present in v0.4.47** — originally
    reproduced in v0.4.39; independent regression failed again on 2026-09-25.
    Moving a recipient away cancels and hides its broadcast; renaming the old
    folder then wrongly makes that cancelled message readable in the new
    scope. Findings sent to main-dev. **Blocking**, not completed.

B4. ~~Packaged header icon/favicons missing in v0.4.47~~ **Fixed** —
    verified 2026-09-27 on installed v0.4.73: both files are in the package
    and serve `image/svg+xml` and `image/x-icon`. Original report: — independent
    screenshot review found the broken header image. The wheel excludes
    dashboard-root favicon.svg/favicon.ico; their URLs return fallback HTML
    with HTTP 200. Include the assets and validate installed image content,
    not only HTTP status. Findings sent to ui-dev.

## Next (started, not yet mergeable)

3. **Remote workspace / DuckCloud — MERGED, shipping incrementally.**
   Verified 2026-09-26: `origin/remote-session` has **zero commits diverged
   from main** (`git log origin/main..origin/remote-session` is empty). The
   big-bang merge risk this document warned about for a week is gone — the
   work landed through a series of PRs instead (#54 local/remote clone via
   the GitHub connector, #58 remote destination picker, #62 exact checkout
   folder), each integrating main as it went.
   Shipped through v0.4.64: Run-on picker (This Mac / Remote), remote
   launch and conversation resume without copying credentials, remote hosts
   under Settings → Remote computers, clone-local/clone-repository options,
   and folder selection meaning the exact checkout directory with folder
   creation inside the picker (superseding v0.4.63's parent-plus-child
   behavior). Live Linux create-folder → authenticated public clone → retry
   verified; production Claude preserved.
   Remaining for DuckCloud proper (design:
   [duckcloud-design.md](duckcloud-design.md)): the setup flow that replaces
   "have a GCP project" with cloud/account/size and hourly prices, cost and
   idle-shutdown controls, the AWS implementation behind the same
   provisioning interface, and reboot/failure/TLS-rotation QA. Note a VM
   reboot terminates processes — never claim continuous execution across one.

4. **Re-home approvals UI** (backend untouched; placement question only —
   TODO.md). Candidates: banner strip atop the selected session's terminal
   pane; browser/desktop notification with Approve/Deny actions; a
   "needs you" filter in the left panel.

## Later (decided direction, not started)

5. **Urgent inbox messages and explicit interruption remain outstanding.**
   The folder-broadcast backend shipped in v0.4.39; Message folder and owner
   inbox labels shipped in v0.4.40. Remaining, with `ui-dev`:
   - **Urgent messages** (owner-requested 2026-09-21, not built): urgency
     changes standing and persistence — notices even while `waiting`,
     re-notices every turn until handled, directive wording with
     anti-derail framing, pinned in the inbox — but NOT delivery speed,
     which the UI copy must state plainly.
   - **"Interrupt session"** (not built): true mid-turn interruption ships
     only as a separate, explicitly named Ctrl-C owner control that
     confirms first and states that in-progress work is lost. Never a side
     effect of marking a message urgent.
   Design: [inbox-awareness-design.md](inbox-awareness-design.md),
   [folder-broadcast.md](folder-broadcast.md). Known gaps carried from the
   backend: queued peer questions and already-idle sessions still need a
   manual check; Codex/Copilot notices unsupported.
6. **PM routines and the approved backlog** (owner-requested 2026-09-20;
   design: [pm-routines-design.md](pm-routines-design.md)). Stage 0 builds
   nothing but a `pm-review` prompt: a long-lived PM session on Claude
   Code's `/loop` proposes items into `BACKLOG.md`, the owner approves in
   its terminal (existing waiting-state Mac notification), and approved
   items dispatch via `duckterm session ask` or a worktree launch.
   Proposal-only invariant: work starts exclusively from owner approval.
   A Backlog tab (one table + approve/decline routes) is built only if
   terminal approval proves annoying in practice; no duckterm scheduler.
7. **Backup — acceptance PASSED 2026-09-24.** Backend v0.4.41, UI
   v0.4.42, cloud bucket provisioned, and the full loop is now verified
   against a real archive, not a fixture:
   - 269 MB uploaded to `gs://rubberterm-20260922-backups/mac/`; archive
     re-downloaded **from GCS** (not the local copy) and restored.
   - `PRAGMA integrity_check` ok, schema v3; 19 sessions / 8 folders / 88
     inbox threads match live (events differed by 8 rows — writes during
     the backup window, as expected); 869 Claude transcript files present;
     **zero credential files** in the archive.
   - The restored DB was opened with the real `HistoryStore` server code
     and sessions came back identifiable by name.
   Independent manual API/UI acceptance also passed on installed v0.4.47
   (2026-09-25), using synthetic data and a real local archive; cloud upload
   was not repeated. The earlier intermittent initial-load timeout did not
   reproduce in this run; that does not establish its root cause.
   Remaining, both deliberate or small: nothing runs the backup
   automatically (owner chose a manual button over a schedule), and GCP
   disk snapshots for the remote workspace VM are still unprovisioned.

8. **Security follow-ups deferred from the 2026-09-20 review** (per the
   `duckterm-bugs` session):
   - Aggregate connection/resource quotas — per-request HTTP
     deadlines/body/header limits and WS frame limits exist, but global
     exhaustion controls are open.
   - Deeper native macOS WebView/bridge review, and a full git-history
     secret audit (the completed scan covered current tracked files only).
9. **Copilot + `duckterm run` collaboration introductions** — both currently
   rely on the manual "Show introduction to paste" path; Copilot's `-p`
   adapter would turn an empty interactive launch into a programmatic one.
   Needs an adapter change before automatic introductions.
10. **Approvals durability** — pending approvals live in memory and return
   `gone` after a restart. Cheap option: persist the registry; decide when
   approval volume makes restart timing annoying in practice.
11. **Watched-mode removal** — frozen and deprecated
   ([terminal-forward-design.md](terminal-forward-design.md)); delete once
   no workflow depends on it (Rubberduck covers that use case).

## Feature requests (user, 2026-09-23)

F1. **Branding: DuckTerm is canonical** (owner decision 2026-09-23).
    Dashboard strings done in `6360437` (tab/browser title, backup
    placeholder). 93 occurrences across 30 files remain, in three tiers —
    do them in this order, not as one blind sed:
    - **Safe text**: README, docs/*, AGENTS.md, RETRO.md, code comments.
      Mechanical; no behavior.
    - **Mac app**: `mac/build.sh` (APP name, binary name, `CFBundleName`),
      menu strings in `main.swift`. Renaming the bundle changes install
      identity — an existing `DuckTerm.app` will not be replaced by a
      `DuckTerm.app`, so the release notes must tell users to delete the
      old one. Coordinate with the release SOP and the artifact names in
      [artifacts-and-releases.md](artifacts-and-releases.md).
    - **Do not rename**: the `duckterm` PyPI package, the `duckterm` CLI,
      `DUCKTERM_*` env vars, `~/.duckterm`, and the GitHub repo — these are
      already "duckterm" and renaming them breaks installs, configs, and
      hook paths wired to absolute locations (RETRO 2026-09-20).
    - Settled: the share domain is `share.duckterm.com`
      ([session-sharing-design.md](session-sharing-design.md)); nothing has
      been shared publicly, so no migration is owed. The domain still needs
      registering before the relay is built.

F2. **Settings button** — the *surface* shipped in v0.4.45 (header Settings
    groups theme, terminal colors, desktop notifications, backup,
    harnesses). What remains is the item it was asked for: **self-update
    to the latest DuckTerm release**, still unbuilt. Distinct from the
    harness Agents tab, which updates the *agent CLIs*; this updates
    DuckTerm itself, and the two should not be conflated in the UI.

F3. **Artifacts — SHIPPED v0.4.55** (PR #36, `77d2f4c`; `main-dev` confirmed released and installed).
    The owner approved the local Artifacts tab beside Inbox and reviewed the
    concrete list/preview layout. Includes session-scoped saved copies,
    Markdown/static HTML/image previews, download and removal. Mac downloads
    use a native Save dialog. DuckCentral and cloud publication remain deferred.
    [Implementation and limits](artifacts.md).

    - Automatic **cooperative registration**: new/resumed agent instructions and
      CLI self/inbox reminders tell agents to register generated deliverables.
      No filesystem scan or claim that every output is detected. This follows
      the owner's latest request for automatic local registration; no separate
      owner acceptance step is required for each artifact.
    - Content is a bounded SQLite snapshot, not a new artifact directory, so
      full archives and sync database copies already include it. A real CLI
      upload and archive restore test verifies the saved bytes survive.
    - Clients upload bytes using their own session credential. Source paths
      are provenance only; the server never follows them. Owner reads/deletes
      require owner authentication; peers cannot access artifact contents.
    - Static HTML runs in an iframe with no sandbox exceptions and a restrictive
      CSP; sanitized Markdown uses the same isolation. Browser tests check
      blocked network/script execution, refresh, downloads and draft retention.
    - Same session/path replaces its saved copy (no prior-version history).
      Stop/archive retains artifacts; explicit session deletion removes them,
      consistent with message pins. Original files and existing backups stay
      intact. File/count/storage limits are documented, not silent eviction.

F4. **Migrate a running session to another harness** (e.g. Claude Code →
    Codex). Hard constraint from
    [accounts-and-handoff-design.md](accounts-and-handoff-design.md): a
    transcript is harness-specific, so this is *not* a resume — it is a
    new session seeded with a summary of the old one. Must be described
    honestly as such; silently implying conversation continuity across
    harnesses would be a lie the user discovers later.

F5. **Meta-harness vocabulary and composition** — the largest item here.
    The user wants installable best-practice suites for SDLC work (e.g.
    "how docs are generated"), and raised that suites may be compatible or
    incompatible with each other, and may include a model router ("for
    these questions use this model"). Requires, in order: (a) a written
    nomenclature — harness (agent CLI) vs meta-harness (suite) vs skill vs
    hook vs router, extending [harnesses.md](harnesses.md)'s existing
    two-meaning definition; (b) a composition/compatibility model for
    installing several suites at once (the existing
    `duckterm-harness.json` already declares compatibility — extend rather
    than replace); (c) only then, the model-router concept, which is the
    least proven piece. Do not build (c) before (a) and (b).

## Designed, not scheduled

12. **Accounts and session sharing** (owner-requested 2026-09-22; design:
    [accounts-and-handoff-design.md](accounts-and-handoff-design.md), with
    live sharing already settled in
    [session-sharing-design.md](session-sharing-design.md) v4). The largest
    architectural shift proposed so far: everything shipped assumes one
    machine, one human, loopback as the boundary. Four stages, in order:
    - **Accounts** — GitHub OAuth on the relay (the only always-on service),
      `users`/`devices` tables, a nullable `owner_user_id` where NULL means
      "this machine's local user", and one principal resolver at dispatch
      with default-deny. Hard constraint: a logged-out install keeps working
      exactly as today — that is the acceptance test. No server-side storage
      of user data; the account answers "who are you", nothing more.
    - **Handoff sharing** (point-in-time, "from here on") — a scoped backup
      of one session (transcript + metadata + checkpoints, credentials
      never, working tree opt-in), client-side encrypted with a fragment
      key, stored on the relay, reconstructed as an independent local
      session on the recipient's machine. No sync after handoff. Requires a
      pre-send preview: sharing a transcript is a disclosure act, and
      pasted secrets are not scrubbed. Resumability is a per-harness
      capability, default unsupported, proven by a real cross-machine test.
    - **Live sharing v1 (watch)** — the relay design, but gated on the
      remote workspace (item 3): a sleeping laptop kills a share, so live
      sharing is only dependable for sessions running on the VM.
    - **Write-capable sharing** — last, and only as owner-approved prompt
      proposals, never raw keystrokes to a PTY.

## Triggers, not plans

Documented upgrade paths we deliberately do not build yet:

- **OS-level isolation for same-user hostile agents** — today's boundary is
  API-level authorization only: session tokens scope cooperating clients,
  not adversaries. Stronger isolation means separate OS identities or
  containers plus an owner broker with explicit capabilities, and
  descriptor-based filesystem operations to close symlink check/use races
  (resolved-path checks don't stop a concurrent local process swapping
  links). Build only if untrusted third-party harnesses/suites become a
  real use case.
- **PTY-relay sidecar (Go/Rust)** — only if profiling shows the asyncio
  byte-pump is the bottleneck at ≈20+ busy terminals.
- **stream-json structured rendering** — only if a review-panel second
  surface (full diffs) is wanted; hooks + transcripts cover today's needs.
- **Email-click approval for backlog proposals** — email starts notify-only
  because the server binds loopback; a public approval endpoint changes the
  trust model. Revisit only if the GCP remote workspace ships an
  authenticated non-loopback surface
  ([pm-routines-design.md](pm-routines-design.md)).
- **Event sourcing** (considered 2026-09-21, deferred) — the append-only
  ledger already exists: the `events` table (hook events, tool use,
  approvals, sub-agent lineage), inbox question threads, checkpoints, and
  agent transcript files. The pattern itself (state derived by replaying
  events, versioning, projections) is never the plan — its cost is
  replay-compatibility on every schema change, not storage (KB-scale text;
  the whole DB is 6 MB). If "how did this project get here?" becomes a
  real need, the build is: retention knobs first (events sweep at 30 days,
  inbox threads at 7 — extend/configure for archived sessions), then a
  per-folder Timeline view that queries tables already being written.
  Depends on the backup story (item 7) — durable history on an
  unbacked-up disk isn't durable.
- **Postgres / replication** — a single-user local tool does not have the
  problem they solve; the backup story (item 7) covers durability.
- **Cross-machine session sharing** — excluded from the session API v1;
  revisit after the GCP remote workspace settles, since it changes the
  "one machine, loopback-only" trust model.

## Release process (changed 2026-09-26)

`release-dev` now owns every release: version bumps, tags, the fresh-worktree
build, the GitHub release with the Mac zip, pipx install, and the dashboard
check. Dev sessions push a branch and hand over the commit SHA, the problem
and fix, the gate log path with pass counts, and any native/E2E QA steps.
Dev sessions do **not** bump `__version__`, tag, run `release.sh` or
`build_package.sh` for a release, create GitHub releases, or merge —
`release-dev` merges after `main-qa`'s QA and the owner's approval via Oracle.

## Standing quality gates (not roadmap items, but they bound every release)

- `scripts/gate.sh` is the only commit gate — no ad-hoc pipelines, no output
  filtering (RETRO 2026-09-20, twice).
- Verify the committed tree (fresh worktree, wheel install, import
  entrypoints) after any selective staging, before tagging.
- App-shell changes get a manual walk of dialogs/clipboard/shortcuts in
  DuckTerm.app — Chromium e2e cannot see the shell.
- Interaction-bug fixes ship with a test at the outermost broken layer.
