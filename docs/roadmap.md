# RubberTerm — Roadmap

As of 2026-09-21, v0.4.27 shipped. Ordered by when work can land, not by
importance. Sources: TODO.md, RETRO.md, design docs, and the active peer
sessions (`duckterm-bugs`, `codex-remote-session`) via the session API.

Done since first draft (2026-09-20): **PR #3 merged** (merge `5a671d7` —
security fixes from
[security-review-2026-09-20.md](security-review-2026-09-20.md), browser-test
CI job, npm audit to zero, release-script guards) and **v0.4.27 released**,
carrying those fixes plus session collaboration
([session-api-design.md](session-api-design.md)).

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

5. **Inbox awareness** (owner-approved 2026-09-21; design:
   [inbox-awareness-design.md](inbox-awareness-design.md)). Sessions do not
   notice their inbox today — deliberate, but folder broadcast changes it:
   an owner message has standing a peer's does not. Stage 0 is zero code
   (a session runs `/loop` with "check `duckterm session inbox`"). Stage 1
   is a turn-end hook notice: one line naming the pending count, fired at a
   pause not mid-work, suppressed while the session is `waiting`, scoped to
   owner broadcasts and accepted-but-unanswered questions, noticed on
   change rather than every turn. Invariant: a notice, never an
   instruction; no injection, no auto-answering. Includes **urgent**
   messages (owner-requested 2026-09-21): urgency changes standing and
   persistence — notices even while `waiting`, re-notices every turn until
   handled, directive wording with anti-derail framing, pinned in the
   inbox — but NOT delivery speed, which the UI must state. True mid-turn
   interruption ships only as a separate, explicitly named "Interrupt
   session" (Ctrl-C) owner control that aborts in-progress work.
6. **PM routines and the approved backlog** (owner-requested 2026-09-20;
   design: [pm-routines-design.md](pm-routines-design.md)). Stage 0 builds
   nothing but a `pm-review` prompt: a long-lived PM session on Claude
   Code's `/loop` proposes items into `BACKLOG.md`, the owner approves in
   its terminal (existing waiting-state Mac notification), and approved
   items dispatch via `duckterm session ask` or a worktree launch.
   Proposal-only invariant: work starts exclusively from owner approval.
   A Backlog tab (one table + approve/decline routes) is built only if
   terminal approval proves annoying in practice; no duckterm scheduler.
7. **Backups** (owner-requested 2026-09-21; details in the data-locality
   section of [architecture.md](architecture.md)). App side **shipped in
   v0.4.34** (`persistence/backup.py`, [backups.md](backups.md)): SQLite
   online backup + checkpoint/transcript archive, credentials excluded,
   atomic private output, optional GCS upload via the user's own `gcloud`
   auth. `main-dev` is verifying restore integrity, credential exclusion,
   and failed-upload retention. Remaining:
   - Schedule it (launchd/cron; Time Machine remains the baseline) — no
     scheduled run exists yet.
   - GCP side: scheduled persistent-disk snapshots for the remote
     workspace VM — a resource policy, zero code, no VM credentials;
     not yet provisioned.
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
