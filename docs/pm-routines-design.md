# PM routines and the approved backlog — design

Status: direction agreed with the owner 2026-09-20 — Stage 0 first, built
minimally; later stages only when Stage 0 shows the specific pain. Supersedes
the earlier draft of this doc, which proposed schema/scheduler/UI up front.
Prerequisite met: session collaboration shipped in v0.4.27
([session-api-design.md](session-api-design.md)).

## The idea in one line

A product-manager session periodically reads the repo and session digests
and **proposes** features, bug fixes, and backlog items; nothing is worked
on until the owner **approves** each item; approved items are dispatched to
long-running worker sessions.

## The invariant (all stages)

**Proposal ≠ execution.** The PM session may only propose. Work starts
exclusively from an owner-approved item, one item at a time — never bulk,
never auto-approved.

## Stage 0 — assemble from what exists, build nothing but a prompt

Every mechanism the feature needs already ships:

| Need | Existing mechanism |
| --- | --- |
| Scheduling | Claude Code's own `/loop` (interval or self-paced) in a long-lived RubberTerm session; tmux persistence keeps the loop alive across server restarts. OS cron + `claude -p` as the non-interactive fallback, and the path for runtimes without self-scheduling. |
| Proposal storage | `BACKLOG.md` in the repo — durable, diffable, git-historied, natively read/written by owner and agents alike. No expiry problem: pending state lives in the file, not in 15-minute session questions. |
| Approval + notification | The PM session asks and goes to **waiting**; the Mac app already raises a native notification for a waiting session. The owner approves by replying in the session's terminal — which is the owner's real approval workflow anyway (see TODO.md on the removed approvals box). |
| Dispatch | `duckterm session ask` to an ongoing worker session, or the owner launches a worktree session from the dashboard with the approved plan as its task. |
| Cross-run memory | The file itself: Proposed / Approved / Declined sections. Declined items stay listed so the next pass does not re-propose them. |

The only artifact to build is the **`pm-review` routine prompt** (a prompt
file or skill), roughly:

1. Read `BACKLOG.md`, `TODO.md`, `RETRO.md`, and peer state via
   `duckterm session discover`.
2. Propose at most 3 new items into `BACKLOG.md` under *Proposed* — title,
   rationale, sketch of a plan — deduping against every existing section.
3. Ask the owner for approval and wait. On approval, move items to
   *Approved* and dispatch (`duckterm session ask`, or name the worktree
   session to launch). Never start work yourself.

Run it: launch a `pm` session in RubberTerm, start `/loop` with the prompt,
answer its notifications. That is the whole feature. Note: `BACKLOG.md`
should be committed, not left untracked — a `git clean` from any session
deletes untracked files (it deleted this doc set once already).

## Stage 1 — build only what Stage 0 proves annoying

Expected friction, and the single earned build if it materializes:

- Approval is typing in a terminal, not clicking a button; there is no
  aggregate "3 pending" badge — only per-session waiting notifications.

If that is the real pain after a couple of weeks of use: add a **Backlog
tab** — one `proposals` table (schema bump), agent `POST /proposals` +
owner approve/decline routes following the session-API conventions, a
badge count beside the Inbox, and the Mac notification fed from it. Scope
stops there. The earlier draft's four-phase design (routines table,
asyncio scheduler, dispatch modes, assignment inbox) is explicitly **not**
the plan.

## Not building, with triggers

- **A duckterm scheduler** — `/loop` + cron cover it. Trigger: routines
  wanted for a runtime with no self-scheduling AND cron proving
  unmanageable in practice.
- **Email** — notify-only, behind a connector credential, if notifications
  beyond the Mac app are ever wanted.
- **Email-click approval** — the server binds loopback; a public approval
  endpoint changes the trust model. Trigger: the GCP remote workspace
  ships an authenticated non-loopback surface.
- **Auto-approval rules** — trigger: a category with a sustained 100%
  approval history in `BACKLOG.md`.
