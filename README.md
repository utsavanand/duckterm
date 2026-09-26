<div align="center">

<img src="web/public/favicon.svg" width="72" alt="DuckTerm logo" />

# DuckTerm

**One workspace for your coding agents: live terminals, nested sessions, shared connectors, and project files.**

[![CI](https://github.com/utsavanand/duckterm/actions/workflows/ci.yml/badge.svg)](https://github.com/utsavanand/duckterm/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/utsavanand/duckterm)](https://github.com/utsavanand/duckterm/releases)
[![License: FSL-1.1-MIT](https://img.shields.io/badge/license-FSL--1.1--MIT-blue)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab)](pyproject.toml)

[Install](#install) · [First session](#your-first-session) · [Features](#features) · [Mac app](#mac-app) · [How it works](#how-it-works) · [Development](#development)

<img src="docs/screenshot.png" alt="DuckTerm dashboard with nested coding agents, a live terminal, the Edit file action, and the connectors panel" width="100%" />

</div>

## What is this?

Claude Code, Codex, and Copilot live in terminal tabs. Running several at once
means alt-tabbing to find the one that's waiting on you, losing track of which
branch each is on, and having no way to ask "what has that session actually
done?"

DuckTerm launches each agent into a tmux-backed PTY it owns and renders it in
the browser with xterm.js — a real terminal you type into, not a transcript
viewer. Around the terminals it shows what a terminal can't:

- which session **needs you right now** (hook-driven state: busy / waiting / idle)
- **context pressure** per session — tokens used, model, and a "checkpoint or
  compact" warning before the window fills
- **approvals as buttons** — permission prompts resolve from the dashboard
- an **Ask Oracle** button — ask questions about all running sessions at once
  ("who's stuck?", "what has refactor-auth done so far?")

Sessions survive server restarts (tmux), run in isolated git worktrees when you
want parallel attempts, and fork — including conversation forks for Claude Code.

## Install

**Recommended: install the CLI, then open the dashboard in your browser.**
The optional [Mac app](#mac-app) uses the same installation and data.

You need macOS or Linux, Python 3.11+, tmux, and at least one installed,
signed-in agent CLI (`claude`, `codex`, or `copilot`). Your agents use their
existing accounts.

On macOS with [Homebrew](https://brew.sh) already installed:

```sh
brew install pipx tmux
pipx ensurepath
```

Open a new terminal so `pipx` and installed commands are on your PATH. On Linux,
install Python 3.11+, pipx, and tmux using your distribution's package manager.
Then install the published wheel:

```sh
pipx install https://github.com/utsavanand/duckterm/releases/download/v0.4.57/duckterm-0.4.57-py3-none-any.whl
duckterm serve
```

The dashboard opens at **http://127.0.0.1:4300**. Leave that terminal running.
DuckTerm is distributed through [GitHub Releases](https://github.com/utsavanand/duckterm/releases),
not currently PyPI; `pipx install duckterm` is not a supported installation path.

## Your first session

1. In the dashboard, choose **New → New session**.
2. Choose an installed agent and the project directory you want it to work in.
3. Launch the session and type your task into its terminal.
4. Use folders to organize agents, **Edit file** for project files, and
   **Connectors** to configure the services your agents need.

For agent event reporting, install hooks for the agent you use **before launching
new sessions**. Run the applicable command in another terminal:

```sh
# Claude Code
duckterm install-hooks --agent claude-code --global
# Codex
duckterm install-hooks --agent codex --global
```

These commands update that agent's user-level configuration. Hook capabilities
vary by runtime; Codex approval prompts must still be answered in its terminal.
Run `duckterm doctor` to check dependencies, hooks, and server connectivity.
See [installation, upgrades, and troubleshooting](docs/installation.md) for help.

## Features

**Terminals, first-class**
- Every session is a live xterm.js terminal: type, paste, Ctrl-C, 5000 lines of
  scrollback. Six color themes, assignable globally, per folder, or per session.
- Launch with any of your installed oh-my-zsh prompt themes per session,
  without touching your `.zshrc`.

**Fleet control**
- Folders nest and drag; each folder opens as a **grid** — iTerm-style splits
  (drag a pane onto another's edge to restack), resize bars, a dock for
  collapsed sessions, a folder switcher.
- Stop is a pause (Resume relaunches — continuing the conversation for Claude
  Code); Archive is final; Delete requires a second click.
- Ask Oracle: one question, answered from a digest of every running session's
  state, goal, and screen.

**The structured layer**
- **Messages view** — the conversation rendered as HTML; select any span of a
  reply, attach a note, and it's sent back to the agent as a follow-up turn.
  Pin individual messages to revisit them from the strip above the terminal.
  Hover a pin icon for a short excerpt; click it to open the message. These
  pins survive restarts and retain a saved copy if the transcript changes.
- **Sub-agent tree** — Task-tool sub-agents nested under their parent, live.
- **Worktrees & forks** — launch into an isolated worktree per attempt; fork a
  session's git state or (Claude Code) its conversation; compare branches.
- **AGENTS.md that learns** — "Suggest from corrections" distills the feedback
  you've given agents (annotations, follow-up prompts) into proposed rules you
  review before saving.
- **Installable meta-harnesses** — register a collection of skills/hooks/sub-agents
  (e.g. uv-suite) and install it into any project from the dashboard, with
  per-meta-harness option pickers and compatibility declarations.
  Contract: [docs/harnesses.md](docs/harnesses.md).

## Session inboxes

Agents can send persistent assignments to other sessions in their shared folder.
Supported runtimes receive a task-end reminder for accepted, unanswered work.
See [inbox delivery and safeguards](docs/inbox-delivery.md).

## Backups

`duckterm backup` archives the database, checkpoints, and Claude/Codex transcripts.
Use `--to PATH` for another local destination or `--to gs://BUCKET/prefix` for
upload with your existing gcloud login. See [backup and restore](docs/backups.md).

## Shared, Gmail, and Google Cloud connectors

Connectors can run on one shared host for local and remote agents. Provider
credentials stay on that host; each agent machine enrolls once with a workspace
certificate. The dashboard also includes personal Gmail (read-only OAuth) and
Google Cloud (an explicitly selected cloud identity).
See [shared connector setup and enrollment](docs/shared-connectors.md).

## Hugging Face connector

Choose **Connect** beside **Hugging Face** for model, dataset, and documentation
search. Public discovery needs no account; Node.js 22+ and npm are required.
An optional token enables authenticated discovery. See the
[Hugging Face setup guide](docs/hugging-face.md) for tokens and discovery settings,
and the [shared connector guide](docs/shared-connectors.md) for central hosting.

## Mac app

On **Apple Silicon Macs running macOS 13+**, install the CLI above, then download
[DuckTerm-0.4.57-macos-arm64.zip](https://github.com/utsavanand/duckterm/releases/download/v0.4.57/DuckTerm-0.4.57-macos-arm64.zip),
unzip it, and move **DuckTerm.app** to **Applications**.

The app opens the dashboard in a native window with a Dock icon and notifications.
It starts the local server when needed, or connects to one already running.
It is ad-hoc signed, **not notarized**; macOS may require you to allow it in
**System Settings → Privacy & Security** after the first launch attempt.
The browser installation works without the app. Intel Mac users can use the
browser or [build the native app from source](mac/README.md).

## How it works

One Python asyncio server (no framework), SQLite for history, tmux for session
persistence. Agents report through their own hook systems (`duckterm
install-hooks`) — session start, tool use, permission requests — which drive
the state badges, approvals, and the sub-agent tree. The terminal is a binary
WebSocket carrying raw PTY bytes to xterm.js; context-pressure numbers are read
from the agent's transcript on disk. Everything runs on 127.0.0.1: GETs are
loopback-gated, state-changing POSTs are token-gated.

DuckTerm is the terminal-forward sibling of
[Rubberduck](https://github.com/utsavanand/rubber-duck), which *watches* agents
you run in your own terminal tabs instead of owning the PTY. Install either or
both.

## Development

```sh
git clone https://github.com/utsavanand/duckterm.git
cd duckterm
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
(cd web && npm ci && npm run build)
DUCKTERM_INSTANCE=dev duckterm serve
```

To run the release gate, install the browser test runtime first:

```sh
(cd web && npx playwright install chromium)
scripts/gate.sh
```

Design docs: [terminal-forward-design.md](docs/terminal-forward-design.md),
[structured-render-design.md](docs/structured-render-design.md).

## License

[FSL-1.1-MIT](LICENSE) — the Functional Source License. You can read, run,
modify, and redistribute DuckTerm for any purpose except offering a
competing product; each release automatically becomes plain MIT two years
after it ships.
