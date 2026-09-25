# DuckTerm — Roadmap

As of 2026-09-25, **v0.4.47** shipped. Ordered by when work can land, not by
importance. Sources: TODO.md, RETRO.md, design docs, and the active peer
sessions (`main-dev`, `ui-dev`, `main-qa`, `feature-remote-session`) via the
session API.

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

## Now (this week)

1. **Fix the folder-broadcast scope regression (B3)** — reproduced again
   against installed v0.4.47; original report and regression sent to main-dev.
2. **Package the missing header icon/favicons (B4)** — independently found
   in v0.4.47; packaging and installed-asset validation need correction.

Branch protection and stop/resume are complete (see shipped items). PR #1 is
closed. Independent bookmark, backup, and menu acceptance results, including
open defects and test limits: [QA report](qa-2026-09-25.md).

## Oracle (owner-requested 2026-09-23, branch `oracle`)

Design: [oracle-design.md](oracle-design.md). Stage 1 implemented, not yet
merged or released: the Ask Oracle topbar button replaces the fleet question
bar; idle agents with pending mail get one fixed reminder pasted into an empty
prompt, on in every folder (kill switch `DUCKTERM_ORACLE=off`); idle Claude
sessions no longer show as waiting. Copilot nudges need its prompt layout
implemented. Later rules (needs-you queue, stale state, file collisions,
scheduled AGENTS.md suggestions) are listed with triggers in the design doc.

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

## Bugs — open

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

B4. **Packaged header icon/favicons missing in v0.4.47** — independent
    screenshot review found the broken header image. The wheel excludes
    dashboard-root favicon.svg/favicon.ico; their URLs return fallback HTML
    with HTTP 200. Include the assets and validate installed image content,
    not only HTTP status. Findings sent to ui-dev.

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
