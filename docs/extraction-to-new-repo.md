# Extraction checklist: forking the terminal-forward take into a new repo

Drafted 2026-06-17. Companion to
[terminal-forward-design.md](./terminal-forward-design.md).

**Executed 2026-07-18 → [utsavanand/duckterm](https://github.com/utsavanand/duckterm).**
Name decided: **duckterm** (repo, PyPI dist, npm scope, CLI command — all free at
creation). The fork happened AFTER the terminal build finished on
`terminal-forward-design` (30 commits), not at its start as originally planned —
so the new repo starts from the branch's finished state, and this checklist was
updated (below) to cover the files that didn't exist when it was drafted:
Terminal.tsx, Messages.tsx, ContextPanel.tsx, AgentsMdModal.tsx, the
`annotations` table + endpoints, `parse_messages`, and `agents/tmux.py`.

**When to fork:** at the START of the terminal build (the first binary-WS +
xterm.js commit) — not before. Until then the new take is "Duckterm + a design
doc," and forking just drags the legacy along. The terminal implementation is
the first code that is genuinely the new product.

**What the new repo is:** a local single-user app that runs your CLI agents in a
PTY it owns, renders them terminal-forward (xterm.js), and layers structured
smarts (approvals, state, sub-agent tree) from the hook/event stream beside the
terminal. No watched mode, no AppleScript terminal tabs.

---

## MOVE — the core that carries over (verified leaf-clean unless noted)

Python core:

- `core/eventbus.py` (87) — event fan-out. Pure core. Move as-is.
- `core/orchestrator.py` (497) — PTY/tmux supervisor, the heart. Move; the
  raw-byte streaming change already landed on the branch.
- `agents/tmux.py` (118) — tmux pane driver the orchestrator uses. Move as-is
  (post-draft addition; not the AppleScript `agents/terminal.py`, which is DROP).
- `core/approvals.py` (179) — approval registry. Move as-is.
- `runtimes/base.py` `runtimes/claude_code.py` `runtimes/generic.py` — the agent
  adapters. Move claude + generic; codex/copilot optional (see DECIDE).
  `claude_code.py` now also carries `parse_messages` (structured transcript
  reader feeding `/sessions/:key/messages` and annotations) — move.
- `harnesses.py` (42) — runtime registry. Move; trim to shipped adapters.
- `git/worktrees.py` `git/gitdetect.py` `git/spotlight.py` — worktree isolation,
  a kept differentiator. Move as-is.
- `persistence/history.py` (746) — SQLite session/event store. Move; it carries
  schema for forks + lineage (sub-agent tree) and the `annotations` table.
- `persistence/checkpoints.py` (261) — move if keeping checkpoints (it's a real
  feature); otherwise DEFER.
- `transport/httpio.py` (132) — HTTP/SSE primitives. Move as-is.
- `transport/websocket.py` (115) — **decision reversed 2026-07-18: move as-is.**
  The branch made it binary + bidirectional and it's what the shipped terminal
  runs on, covered by the terminal e2e specs. Swapping in a WS library now would
  replace working tested code with an unproven integration. Trigger to revisit:
  a masking/fragmentation/backpressure bug in the wild.
- `helpers/paths.py` `helpers/security.py` `helpers/metrics.py` — small support.
  Move what the moved modules import; drop the rest.
- `llm/summarizer.py` `llm/insights.py` — outcome summaries. Move if keeping
  history summaries; otherwise DEFER.

Web (TypeScript/React):

- `web/src/api.ts` `useEventStream.ts` `sessions.ts` `types.ts` `ui.tsx`
  `useTheme.ts` `main.tsx` — the app spine. Move.
- `web/src/AgentTree.tsx` (621) `ForkTree.tsx` — the lineage tree UI; the
  sub-agent tree extends this. Move.
- `web/src/Approvals.tsx` `SessionDetail.tsx` `App.tsx` — move; SessionDetail
  already hosts the xterm pane on the branch.
- `web/src/Terminal.tsx` `Messages.tsx` `ContextPanel.tsx` `AgentsMdModal.tsx` —
  the terminal-forward UI built on the branch (xterm pane, structured Messages
  view + annotation send-back, context panel, cross-agent AGENTS.md editor).
  Move; this IS the product surface.
- `web/src/LaunchModal.tsx` `ForkModal.tsx` `CompareModal.tsx`
  `NewFolderModal.tsx` `SnapshotsModal.tsx` — move the ones whose features
  survive (launch, fork). Snapshots → DECIDE.
- `web/src/LiveOutput.tsx` — **DO NOT move.** Replaced by Terminal.tsx.
  Reference only.
- `web/src/Pulse.tsx` — RESOLVED: already deleted on the branch.

Infra:

- `.claude/rules/*.md` (the slop guardrails) — move; they're good.
- `scripts/check.sh` `scripts/slop_check.py` `scripts/pre-commit` — move; the
  quality gate is worth keeping.
- `pyproject.toml` `web/package.json` — adapt (new name, add WS lib + xterm.js,
  drop unused deps).
- `.github/workflows/ci.yml` — adapt.

---

## DROP — legacy the new take does not carry

- `agents/terminal.py` (334) — the AppleScript open/close/focus/answer-by-tty
  path. The whole macOS-fragility reason for the pivot. Verified leaf: imported
  ONLY by `server.py` and `cli.py`. Delete, and delete its call sites.
- **Watched mode — REVISED 2026-07-18 after the confirm-during-the-build check.**
  The original plan assumed launched sessions could read `SubagentStart/Stop`
  (and the rest of the smarts) from the transcript instead of hooks. That
  alternative was never built: on the branch, approvals, state, and the
  sub-agent tree for LAUNCHED sessions are still powered by the installed hooks
  POSTing to `/events`, and `duckterm run` depends on `/heartbeat` to bind the
  current terminal. So the hook layer MOVES: `agents/hooks_install.py`, the hook
  script (renamed `duckterm-hook.sh`), `install-hooks`/`uninstall-hooks`,
  `doctor.py`, `/events` ingestion, `/heartbeat`. What actually gets dropped is
  the AppleScript tab management (above) and its tty call sites: `/terminals`,
  `/sessions/:key/focus`, close-tab-on-stop/delete, answer-prompt-by-tty.
  Transcript-as-event-source remains a future simplification, not a fork
  precondition.
  - **2026-07-19: launched-only enforced.** Decided with the user: watching
    sessions you started yourself is Rubberduck's product, not Duckterm's.
    `/events` ingest drops hook events that carry no `session_key` (the hook
    only sends one when `DUCKTERM_SESSION_KEY` was in the agent's env, i.e.
    Duckterm started it), and a blocking approval from an unknown session gets
    `id: null` so the hook falls through to the agent's own prompt. The hook
    layer itself still moves — launched sessions depend on it.
- `mac/` Swift shell — DECIDE, don't auto-move (see below).
- `scripts/seed_demo.py` — demo seeding; regenerate fresh if needed.

---

## REWRITE — don't copy, rebuild clean

- `server.py` (1826) — **the biggest trap. Do NOT copy wholesale.** The routing
  table mixes core and legacy. Port handlers selectively: keep events, heartbeat,
  sessions, launch, fork, approvals, diff, worktree, terminal WS attach + resize,
  messages, annotations, snapshots, checkpoints, folders, agents-md; leave
  behind every `*_terminal_by_tty` call site and the `/terminals` +
  `/sessions/:key/focus` routes (REVISED 2026-07-18: hooks/heartbeat stay —
  see DROP).
- `cli.py` (452) — rebuild around the surviving commands: `serve`, `launch`,
  `run`, `dashboard`, `install-hooks`/`uninstall-hooks`, `doctor`, `snapshot`
  (all carry; only AppleScript-dependent behavior goes).

---

## DECIDE — resolved 2026-07-18 at fork time

- **codex / copilot adapters** — KEEP all four (generic, claude-code, codex,
  copilot). They exist, are tested, and prove "any CLI agent"; dropping working
  code only to re-add it is churn.
- **tmux vs pure-PTY** — KEEP both; tmux stays the default for persistence
  (it's also what the browser-resize fix depends on).
- **Mac shell (`mac/`)** — KEEP the Swift webview, renamed Duckterm. Revisit
  packaging only if a concrete packaging problem appears.
- **Snapshots, checkpoints, LLM summaries** — KEEP. Snapshot restore is a plain
  argv (`restore_command_for`), not AppleScript-coupled. Pulse: gone (deleted
  upstream on the branch).
- **New name** — **duckterm.** Keeps the duckterm brand family (Duckterm =
  classic/no terminal, Duckterm = terminal-forward), one typable word, and was
  free on GitHub/PyPI/npm at creation. The orchestra shortlist (Concerto, Tutti,
  Prospero, Calliope) was all registry-taken.

---

## Suggested fork mechanics

1. Fresh repo, not a GitHub fork (clean history, no legacy baggage in the tree).
2. Copy the MOVE list into the new structure; do NOT copy DROP/REWRITE files.
3. First commit: the moved core + a stub server that only does
   events/sessions/launch. Green CI (port `check.sh`).
4. Second commit: the terminal — WS library, raw-byte streaming in the
   orchestrator, `/resize`, xterm.js component. This is the real product start.
5. Carry over `.claude/rules` + the pre-commit gate from commit 1 so quality
   holds from the start.

---

## State at extraction (2026-07-18; e2e repaired 2026-07-19)

Green: ruff, black, mypy --strict, Python tests, web unit tests, slop-check,
web build, and the full Playwright e2e suite. CI (`.github/workflows/ci.yml`)
runs the python + web layers; `scripts/check.sh` adds the e2e layer.

The extraction inherited 11 failing e2e specs from rubber-duck's
`terminal-forward-design` branch (its pre-commit gate ran `--no-ui`, so they
went unnoticed there). Repaired 2026-07-19:

- The attach snapshot (`tmux.capture_screen`) painted the full 120x40 pane
  including trailing blank rows, scrolling short static output out of the
  xterm viewport, and joined lines with bare LF (subprocess text mode had
  normalized CRLF). It now trims trailing blank rows and joins with CRLF.
- 8 specs were rewritten for the three-pane layout (the `All (n)` /
  `Archived (n)` filter buttons no longer exist; archived rows just leave the
  list). archive.spec unarchives via the API — the redesigned UI has no
  archived view yet, so the row-level Unarchive button is unreachable. If
  archive is meant to be reversible from the UI, that view needs building.
- terminal-rawmode.spec depended on a hand-made `/tmp/rawtui.py` that no
  longer existed; the spec now writes its raw-TUI program itself. Its locators
  are scoped to the visible terminal slot (terminals stay mounted per agent).
- snapshot.spec was deleted, along with the dead `SnapshotsModal.tsx` /
  `CompareModal.tsx` and their api wrappers: the redesign never mounted them,
  so snapshots are backend-only (covered by tests/runtime/test_snapshot_flow).
  Restoring a snapshot UI is a product decision, not a test fix.
