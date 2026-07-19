# Harnesses: runtime adapters and installable suites

"Harness" means two things in Duckterm. Both have a concrete contract.

## 1. Runtime adapters — how Duckterm drives and observes one agent CLI

Ported from rubber-duck's architecture doc; the unification it planned is
**done** here. One adapter class per agent owns both halves:

- **drive** — `launch_command`, `detect_state`, `locate_transcript`,
  `read_transcript` (the `AgentRuntime` methods in `runtimes/base.py`).
- **observe** — `hook_spec`: where the agent's hook config lives and how to
  merge/strip Duckterm's entries (None for agents with no hook system).

The registry in `harnesses.py` maps `name -> adapter class` and is the single
source of truth: the CLI's `install-hooks --agent` choices, the New-session
agent picker, `_build_runtime`, and checkpoint transcript reading all resolve
through it.

**Onboarding a new agent CLI:** implement the `Harness` contract in
`runtimes/<name>.py`, add one `REGISTRY` entry. It then appears in the agent
picker and install-hooks, and gets state badges, checkpoints, and
conversation-fork support. An agent with no hook system still works via the
generic runtime — driven in a PTY, just without hook-powered smarts.

Shipped adapters: `claude-code`, `codex`, `copilot`, `generic`.

## 2. Installable harnesses — suites of skills, hooks, and sub-agents

A suite like [uv-suite](https://github.com/utsavanand/uv-suite) bundles
skills, hooks, sub-agents, guardrails, and personas, and installs them into a
project's `.claude/` (or globally). Duckterm manages these from the
**Harnesses** button in the topbar: register a suite by its directory path,
then install it into any project folder.

The contract is `duckterm-harness.json` at the suite's root:

```json
{
  "name": "uv-suite",
  "description": "Agents, skills, hooks, guardrails, and personas for Claude Code",
  "install": ["./install.sh", "--project", "{dir}"],
  "uninstall": ["./uninstall.sh"],
  "args_choices": { "--persona": ["professional", "sport", "auto", "spike"] }
}
```

- `install` is an argv template: `{dir}` is replaced with the target
  directory; a relative program path resolves against the suite's directory;
  the process runs with **cwd = the target directory**.
- `uninstall` is optional; suites that declare it get an Uninstall button.
- `args_choices` maps a flag to its allowed values; the modal renders one
  picker per flag and appends `flag value` to the argv (uv-suite's personas).
- **Fallback:** a directory with an `install.sh` but no manifest is accepted
  as `{name: <dirname>, install: ["./install.sh"]}`. Since cwd is the target,
  any installer that defaults to `$(pwd)` works unmodified — uv-suite does.
- Extra args typed in the UI (e.g. `--persona sport`) are appended to the
  argv.

Endpoints: `GET /harnesses`, `POST /harnesses/register {path}`,
`POST /harnesses/:name/install {dir, args}`, `DELETE /harnesses/:name`.
Registered suites live in the `harnesses` table (name + path); the manifest is
re-read from disk on every use so edits take effect without re-registering.

Installers are local scripts the user registered by path and run as the user —
the same trust level as launching an agent in a terminal.

## The observation loop (AGENTS.md phase 2)

The AGENTS.md editor's **Suggest from corrections** button closes the loop
between what you tell agents and what the folder's shared instructions say:

1. Duckterm already records two correction signals: **annotations** (spans of
   a reply you pushed back on, Messages view) and **follow-up prompts** (every
   `UserPromptSubmit` after a session's first — the first is the task, later
   ones are steering).
2. `POST /agents-md/suggest {dir}` gathers those signals from sessions that
   worked in the folder (capped at the newest 80) and asks the summarizer
   backend (`DUCKTERM_SUMMARIZER_CMD` / auto-detected `claude -p`) to extract
   at most 5 durable, general rules — task-specific feedback is filtered out
   by the prompt.
3. Proposals land in the editor under a "Suggested from corrections" heading
   for you to review, edit, and save. Nothing is written without the save.

There is no background job: the loop runs when you ask for it, and the human
approves every rule that lands.
