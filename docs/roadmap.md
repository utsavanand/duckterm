# DuckTerm — Roadmap

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

## Bugs (user-reported 2026-09-23, fix before new features)

B1. **Messages panel shows the previous session's transcript.** Reported:
    "if I switch back to a previous session the messages window should show
    things corresponding to that". Root cause found: `<Messages>` is
    rendered without a React `key` in SessionDetail, so React reuses the
    component instance across session switches. Its fetch effect does
    depend on `sessionKey` and re-fires, but `messages` state (and the
    `back`/turn cursor) survive the switch, so the old transcript renders
    until the new fetch resolves — and the turn cursor can point into the
    wrong session's turns. Fix: `key={sessionKey}` on the component (forces
    a fresh instance), and clear `messages`/`loaded`/cursor at the top of
    the effect so no stale frame is ever shown. Test: switch A→B→A and
    assert the panel never displays A's turns while B is selected.

B2. **Connectors are configured but not usable.** Reported: "I have
    multiple connectors but I don't know if I can really use them." Needs
    diagnosis before a fix: is the MCP server registered but not reaching
    the agent, is the state unclear in the UI (available vs configured vs
    connected — already on connectors-dev's list), or do connectors only
    reach local claude-code/codex and not remote/other harnesses (a known
    gap main-dev raised)? Assign diagnosis to connectors-dev.

B3. **Folder-rename scope bug in v0.4.39** — reproduced by main-qa in the
    folder-broadcast/session-API area; findings sent to main-dev. Blocking:
    more features are landing on top of this code.

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
   - Interactive Mac acceptance in the **DuckTerm Test app** (purple TEST
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
7. **"Back up to remote" button** (owner decision 2026-09-22: manual, not
   scheduled). The CLI shipped in v0.4.34; the owner wants a topbar button
   that runs it on demand rather than a cron/launchd schedule — backups
   happen when the user decides, with visible progress and result.
   - Topbar button ("Back up to remote"), owner-token POST that runs the
     existing `backup.create(destination)`; destination configured once
     (local path or `gs://bucket/prefix`) and remembered.
   - It is a long operation (a real run: 242 MB, ~1 s, 0600, zero
     credential files — verified 2026-09-22) — the button must show
     in-progress state and report the resulting archive path, or the
     error, without blocking the dashboard.
   - No scheduler. Time Machine remains the baseline; a user who wants
     automation can still cron the CLI.
   - GCP side (separate, still open): scheduled persistent-disk snapshots
     for the remote workspace VM — a resource policy, zero code, no VM
     credentials; not yet provisioned.
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

F2. **Settings button (web + Mac app).** A top-level Settings surface; the
    first item is "update the software" (self-update to the latest
    DuckTerm release). Related to but distinct from the harness Agents
    tab, which updates the *agent CLIs* — this updates duckterm itself.

F3. **Folder artifacts.** Attach artifacts (markdown/HTML) to a folder, and
    let an agent that generates one *recommend associating it* with the
    folder. Mac app renders markdown at minimum, HTML if cheap. Design
    question to settle first: is an artifact a file reference on disk
    (cheap, always current, dies if the file moves) or a stored copy
    (durable, snapshot semantics, needs storage + retention)? Recommend
    file reference in v1.

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

## Standing quality gates (not roadmap items, but they bound every release)

- `scripts/gate.sh` is the only commit gate — no ad-hoc pipelines, no output
  filtering (RETRO 2026-09-20, twice).
- Verify the committed tree (fresh worktree, wheel install, import
  entrypoints) after any selective staging, before tagging.
- App-shell changes get a manual walk of dialogs/clipboard/shortcuts in
  DuckTerm.app — Chromium e2e cannot see the shell.
- Interaction-bug fixes ship with a test at the outermost broken layer.
