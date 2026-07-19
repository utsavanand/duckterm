# Duckterm

Run your CLI coding agents (Claude Code, Codex, Copilot, or any CLI) in real
terminals that Duckterm owns, rendered in the browser with xterm.js — with the
structured layer a raw terminal can't show beside them:

- **Approvals as buttons** — permission prompts from the agent's hooks resolve
  from the dashboard, not by hunting for the right terminal tab.
- **Sub-agent tree** — the Task-tool sub-agents an agent spawns, nested under
  their parent.
- **Messages view** — the conversation rendered as HTML; select any span of a
  response, attach a note, and it's sent back to the agent as a follow-up turn.
- **Worktree isolation & forks** — launch sessions into their own git worktrees;
  fork a session's conversation and compare branches.
- **AGENTS.md that learns** — one shared instructions file per folder, read by
  every agent; "Suggest from corrections" distills the feedback you've given
  agents (annotations, follow-up prompts) into proposed rules you review and
  save.
- **Installable harnesses** — register a suite of skills/hooks/sub-agents
  (e.g. uv-suite) by path and install it into any project from the dashboard.
  Contract: [docs/harnesses.md](docs/harnesses.md).

Duckterm is the terminal-forward sibling of
[Rubberduck](https://github.com/utsavanand/rubber-duck). Rubberduck observes
agents in your own terminal tabs; Duckterm owns the PTY and renders the
terminal itself. Install either or both.

## Install

```sh
pipx install duckterm   # or: pip install duckterm
```

## Use

```sh
duckterm serve          # start the server + dashboard (localhost)
duckterm run claude     # run an agent in the current terminal, tracked
duckterm launch claude  # launch an agent into a Duckterm-owned PTY
duckterm dashboard      # open the dashboard
duckterm install-hooks  # wire agent hooks (approvals, state, sub-agent tree)
```

The dashboard shows each session's live terminal (type directly into it), a
Messages toggle for the rendered conversation, and the context panel.

## Requirements

- macOS or Linux, Python ≥ 3.11
- tmux (sessions survive server restarts)
- The agent CLIs you want to run (`claude`, `codex`, ...) on your PATH — agents
  run under your own subscription; Duckterm never calls a model API itself.

## Development

```sh
pip install -e ".[dev]"
(cd web && npm install)
scripts/check.sh        # lint, types, python tests, web tests, e2e
```

Design docs: [docs/terminal-forward-design.md](docs/terminal-forward-design.md),
[docs/structured-render-design.md](docs/structured-render-design.md).

## License

MIT
