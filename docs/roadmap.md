# RubberTerm — Roadmap

As of 2026-09-22, **v0.4.39** shipped. Ordered by when work can land, not by
importance. Sources: TODO.md, RETRO.md, design docs, and the active peer
sessions (`main-dev`, `ui-dev`, `main-qa`, `feature-remote-session`) via the
session API.

Shipped since this doc was first written (2026-09-20 → 22):

- **Security + CI** (PR #3, `5a671d7`) and session collaboration — v0.4.27.
- **Backup CLI** — v0.4.34. `duckterm backup [--to PATH|gs://BUCKET]`:
  SQLite online backup, checkpoints/snapshots, Claude+Codex transcripts,
  credentials excluded, 0600 archive, GCS upload via the user's own gcloud;
  a failed upload retains the local archive. [backups.md](backups.md).
- **Q&A survives stop/resume** — v0.4.36.
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

## Now (this week)

1. **Enable branch protection / rulesets on `main`** requiring pull requests
   and passing Python, web, and browser checks. Verified 2026-09-20: no
   rulesets, main unprotected. The release-script guard does not enforce
   GitHub merge rules or prevent publishing outside the script.
2. **Session Q&A survives stop/resume** — implemented for v0.4.36.
   Stop revokes credentials while retaining queued/accepted questions and their
   original ≤15-minute deadlines. Resume issues a fresh credential; terminate,
   archive, deletion, and scope-loss moves still close exchanges. Regression
   coverage includes late child exit events and expiry while stopped. No schema
   change. Durable assignments remain the separate Stage 1 concept in
   [pm-routines-design.md](pm-routines-design.md).

## Next (started, not yet mergeable)

3. **Remote workspace on GCP** (branch `remote-session`, latest `250db8b`).
   End state: **persistent agents/terminals on a per-user workspace VM, not
   a graphical desktop**. Decided UX: New session → "Run on: This Mac /
   Remote"; Existing session → "Move to remote"; host setup lives in
   Settings → Remote computers, not the primary workflow.
   Before merge:
   - Build the new session-level remote UX and **session migration**
     (transfer project changes + supported conversation history, resume
     remotely, keep the local session until success) — newly requested,
     not implemented or proven.
   - Interactive Mac acceptance in the **RubberTerm Test app** (purple TEST
     icon, separate bundle identity and local instance; built, branding
     changes uncommitted), then promote.
   - Reboot/failure/TLS-rotation QA and the overnight persistence result
     (scheduled Sep 21 13:11 UTC). A VM reboot terminates processes — never
     claim continuous execution across reboot.
   - Railway/Porkbun live QA (GitHub verified first, user-selected).
   - Reconcile with main, which now includes the released session-API and
     security work.
   Cost: ~$40–50/month for two VMs before model usage/tax; budget alerts do
   not cap spend. Open operational items: manual TLS renewal, SQLite
   retention planning for production.
4. **Re-home approvals UI** (backend untouched; placement question only —
   TODO.md). Candidates: banner strip atop the selected session's terminal
   pane; browser/desktop notification with Approve/Deny actions; a
   "needs you" filter in the left panel.

## Later (decided direction, not started)

5. **Folder broadcast + inbox awareness — UI half outstanding.** Backend
   shipped (v0.4.38/39, above). Remaining, with `ui-dev`:
   - "Message folder" interface (textarea, who-will-receive list, result
     summary) and the owner-label treatment in InboxView.
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
7. **Backup scheduling** — the CLI shipped in v0.4.34 (see the list above);
   nothing runs it automatically yet. Remaining:
   - A launchd/cron entry for a daily local run, plus `--to gs://…` once a
     bucket is chosen. Time Machine remains the baseline.
   - GCP side: scheduled persistent-disk snapshots for the remote
     workspace VM — a resource policy, zero code, no VM credentials;
     not yet provisioned. Verified 2026-09-22: a real run produced a
     242 MB 0600 archive with zero credential files.
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

## Standing quality gates (not roadmap items, but they bound every release)

- `scripts/gate.sh` is the only commit gate — no ad-hoc pipelines, no output
  filtering (RETRO 2026-09-20, twice).
- Verify the committed tree (fresh worktree, wheel install, import
  entrypoints) after any selective staging, before tagging.
- App-shell changes get a manual walk of dialogs/clipboard/shortcuts in
  RubberTerm.app — Chromium e2e cannot see the shell.
- Interaction-bug fixes ship with a test at the outermost broken layer.
