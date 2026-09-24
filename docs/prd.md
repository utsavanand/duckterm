# DuckTerm — Product Release Document (PRD)

Status: current as of v0.4.27 (2026-09-21). Repo: `duckterm`. Companion docs:
[architecture.md](architecture.md), [roadmap.md](roadmap.md).

## Problem

Claude Code, Codex, and Copilot run in terminal tabs. Running several at once
means alt-tabbing to find the one waiting on you, losing track of which
branch each is on, and having no way to ask "what has that session actually
done?" A raw terminal cannot show approval prompts as actionable UI,
context-window pressure, or a sub-agent tree.

## Product

DuckTerm launches each agent CLI into a tmux-backed PTY it owns and renders
it in the browser with xterm.js — a real terminal you type into, not a
transcript viewer — with a structured layer beside it driven by the agents'
own hook systems.

Agents run under the **user's own subscription**; DuckTerm never calls a
model API for the agents themselves. Everything runs on `127.0.0.1`.

## Users

- Primary: a developer running 2–15 concurrent CLI coding agents on one
  machine (macOS or Linux) who needs to know which session needs them.
- Secondary: the same developer on a remote workspace (GCP VM) reattaching
  over SSH (in progress — see roadmap).

## Requirements (shipped in v0.4.27)

**Terminal**
- Live xterm.js terminal per session: type, paste, Ctrl-C, 5000 lines
  scrollback. Binary WebSocket carries raw PTY bytes both directions.
- Sessions survive server restarts via tmux; resize propagates to the PTY.
- Six color themes; oh-my-zsh prompt themes selectable per session without
  touching `.zshrc`.

**Fleet control**
- Nested, draggable sidebar folders; each folder opens as a grid with
  iTerm-style splits, resize bars, a dock for collapsed sessions.
- Session lifecycle: Stop is a pause (Resume relaunches, continuing the
  conversation for Claude Code); Archive is final; Delete requires a second
  click and tears down tmux pane + worktree. Terminated rows offer only
  end-state actions.
- Fleet chat: one question answered from a digest of every running session's
  state, goal, and screen.
- Hook-driven state badges (busy / waiting / idle) and per-session context
  pressure (tokens used, model, checkpoint/compact warning) read from the
  agent's transcript on disk.

**Structured layer**
- Approvals resolve from the dashboard (backend endpoints + zombie expiry;
  the right-panel UI is temporarily removed pending a better home — TODO.md).
- Messages view: the conversation as HTML; select a span, attach a note, and
  it returns to the agent as a follow-up turn.
- Sub-agent tree: Task-tool sub-agents nested under their parent, live.
- Worktrees & forks: isolated git worktree per attempt; fork git state or
  (Claude Code) the conversation; compare branches.
- AGENTS.md "Suggest from corrections": distills annotations and follow-up
  prompts into proposed rules the user reviews before saving.
- Installable harnesses: register a suite of skills/hooks/sub-agents
  (contract: `duckterm-harness.json`, see [harnesses.md](harnesses.md)) and
  install it into any project from the dashboard.
- Connectors: one switch wires GitHub / Railway / Porkbun MCP servers into
  claude-code and codex configs, resolving credentials from existing CLI
  logins or the macOS Keychain — never plaintext in harness configs.

**Session collaboration (shipped in v0.4.27)**
- Each session gets a card (purpose, activity, state, deliverables), scoped
  discovery of peers in its sidebar folder tree, and a durable
  question/answer inbox. Owner UI: Inbox tab with pending-count badges.
- Design and limits: [session-api-design.md](session-api-design.md). The
  startup migration (schema 3) enrolls existing ongoing sessions.

**Security hardening (shipped in v0.4.27, PR #3)**
- `shlex.quote` terminal-command construction, loopback Host + same-origin
  gates, resolved-path/symlink confinement for dashboard and AGENTS.md
  routes, HTTP/WS size and deadline limits, atomic 0600 secret writes, npm
  audit at zero. Details:
  [security-review-2026-09-20.md](security-review-2026-09-20.md).

**Mac app**
- Swift WKWebView shell over the same dashboard with native notifications,
  full menu/clipboard/dialog bridging. Ad-hoc signed zip on GitHub Releases.

## Non-goals

- Calling model APIs directly or replacing the agent CLIs' billing.
- Structured chat-first rendering via `--output-format stream-json`
  (CodeLayer's approach) — revisit only as a second surface.
- Containing a hostile agent: session-API authorization is API-level, not an
  OS sandbox. Agents running as the same OS user can read each other's files.
- Postgres or any distributed/replicated storage — single-machine SQLite;
  see the data-locality section of [architecture.md](architecture.md).
- Cross-machine session sharing, automatic terminal injection, or
  auto-answering inboxes with a model.
- Watched mode (observing agents launched outside DuckTerm) is frozen and
  on the deprecation path; it lives on in the sibling project Rubberduck.

## Approved direction, not yet built

- **PM routines + approved backlog**
  ([pm-routines-design.md](pm-routines-design.md)): a product-manager
  session on a recurring loop proposes features/bugs/backlog items from
  the repo and session digests; every item requires owner approval before
  it is dispatched to long-running worker sessions. Stage 0 is assembled
  entirely from existing mechanisms (Claude Code `/loop`, `BACKLOG.md`,
  waiting-state Mac notifications, `duckterm session ask`) — the only new
  artifact is the `pm-review` prompt. A Backlog approval tab is built
  later only if the terminal-approval workflow proves annoying.
  Invariant: the PM session can propose, never execute.
- **Backups** ([architecture.md](architecture.md) data-locality section):
  a `duckterm backup` command producing one consistent archive, optionally
  uploaded to GCS in the existing GCP project; disk-snapshot schedules for
  the remote VM.

## Release process and gates

- Version source of truth: `src/duckterm/__init__.py`. Release via
  `scripts/release.sh` (SOP: [release-sop.md](release-sop.md)); it rejects
  dirty worktrees, non-main branches, and commits without passing CI.
- `scripts/gate.sh` runs the whole gate (lint, types, pytest, vitest,
  Playwright) with `set -e` and no output filtering — the exit code is the
  verdict (see RETRO.md for why).
- `scripts/build_package.sh` builds the web dashboard into the wheel; wheel
  contents are verified before publishing (0.3.4 / 0.4.17 / 0.4.18 lessons).
- `scripts/release_preflight.py` and, for schema-migrating releases,
  `scripts/rehearse_session_upgrade.py` (isolated old→new upgrade rehearsal;
  verified 0.4.19 → schema 3 with unchanged agent PIDs). Pending approvals do
  not survive a server restart — activate between approval flows.

## Current release state (2026-09-21)

- Shipped: v0.4.27 on PyPI/GitHub Releases — includes the PR #3 security
  fixes (merge `5a671d7`) and session collaboration; collaboration is live
  on this machine.
- In flight: remote two-VM workspace on the `remote-session` branch,
  pre-merge — decided UX: New session → "Run on: This Mac / Remote",
  Existing session → "Move to remote", host setup under Settings → Remote
  computers (see [roadmap.md](roadmap.md)). Main branch protection still
  off.

## License

FSL-1.1-MIT: use/modify/redistribute freely except a competing product; each
release becomes MIT two years after it ships.
