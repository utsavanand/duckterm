# Persistent collaboration work

Implementation: `feature/collaboration-work`, built from Architect's F12 design
in `collaboration-reliability-design.md`. QA and release are pending.

Replies and work have separate lifecycles. A reply still answers a question;
it never marks the associated implementation complete. Ordinary questions do
not create work items. Explicit assignment metadata avoids guessing which
sentences in an inbox are tasks.

## Session commands

```sh
duckterm session ask SESSION_ID 'Fix the reported problem' --work-title 'Expected outcome'
duckterm session accept REQUEST_ID --work-title 'Expected outcome'
duckterm session work list
duckterm session work get WORK_ID
duckterm session work update WORK_ID --state in_progress --note 'Reproduced; implementing fix'
duckterm session work update WORK_ID --state blocked --blocker 'Owner must choose destination'
duckterm session work update WORK_ID --state done --evidence 'https://github.com/org/repo/pull/42'
duckterm session work update WORK_ID --assign SESSION_ID --note 'Handoff context'
```

`ask --work-title` creates a work item when the recipient accepts. `accept
--work-title` tracks an existing request, including one already accepted.
Repeated acceptance returns the same work item. `work create TITLE --request
REQUEST_ID` can explicitly track an accessible request. `work list --before
CURSOR` follows `next_cursor` to older items.

The states are proposed, accepted, in_progress, blocked, done, and dropped.
Only the assignee or owner can advance work; the requester can hand it off or
drop it with a reason. Reassignment records a note and returns it to proposed.
Declining the original assignment returns it for reassignment unless it has
already been handed off. Deleting the assignee preserves the outcome and
history. Scope changes remove the old assignee rather than leaking work into
a different shared root. Test-session cleanup removes its work data.

Done requires a commit SHA, HTTPS reference, PR number, or version. This records
reported evidence; it does not verify a GitHub merge or automatically publish a
release. A named blocker is required for blocked, and a reason for dropped.
Identical retries do not reset staleness or duplicate completion updates.

## Nudges and updates

Accepted or in-progress work becomes stale after an hour without an update.
Oracle checks it on its existing sweep, using the existing idle, empty-prompt,
and typing-quiet gates. Proposed handoffs become eligible after five minutes.
No new scheduler or background worker is added. Reminders contain fixed text,
not the task title or peer's instructions. Stored notice timestamps and counts limit unchanged work to two reminders,
with four hours between them, including across restarts and task-end notices.
After the second reminder the requester receives an attention-needed update;
further reminders stop until progress changes. Work-status maintenance keeps
running when Oracle nudges are disabled.
Busy agents are not interrupted. Blocked work is displayed for a decision,
not repeatedly nudged as if the assignee could resolve it alone.

The requester receives an honest stale-state update even if the assignee has
not reached a turn boundary. Unread identical stale snapshots are coalesced;
actual progress history remains separate. No progress text is invented.

`session get REQUEST_ID` includes the linked work state when visible.
`session inbox` includes `work` and `work_updates`. Reading acknowledges updates,
but does not accept assignments, advance work, or send a receipt. Acceptance
sends an accepted update; replying sends an answered update, not a done update.
Work completion sends the completion update with its recorded evidence.

Use `ask --parent-request REQUEST_ID` when relaying an incoming open request.
Status updates walk at most five requests, tolerate missing/closed links and
cycles, and notify ancestors without forwarding downstream answer text. A
child's completion does not automatically close its parent's work.

## API for the dashboard

Owner-authenticated routes:

- `GET /work?before=CURSOR`: `{work: [...], next_cursor}`.
- `GET /work/w-ID`: one item with `history`.
- `PATCH /work/w-ID`: state, note (500 UTF-8 bytes), blocker, evidence,
  owner_session, or owner_authorized (boolean).

Session-scoped equivalents live under `/api/v1/session/work`, with POST for
`{origin_request_id, title}`. Agent reads and writes are limited to the original
requester/current assignee in the captured shared root. Only owner-authenticated
PATCH may set owner_authorized; a quoted claim from another session cannot.
Work status is bookkeeping, not permission to execute a task.

Existing owner inbox responses include work and unread work_updates without
marking them read. The Work UI is a separate preview/review handoff to UI-dev.
Schema v5 adds work, work history, update delivery, and parent/title request
metadata. v4 is reserved for Focus pins (PR #78).

## Deliberately separate follow-ups

- Work-view visual implementation and owner review (UI-dev).
- Automatic verified GitHub-merge closure: this requires a trusted integration;
  a peer saying a PR merged must not silently close an outcome.
- Existing Oracle wording/guide refresh and general inbox-nudge restart memory
  remain in PRs #77 and #75. This implementation preserves their ownership.
