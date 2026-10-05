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

**Hook identity.** The hook names its session from `DUCKTERM_SESSION_KEY`,
which DuckTerm puts in each agent's environment at launch. Codex 0.159 broke
that assumption. It runs every session's hooks in one shared daemon (`codex
app-server --managed-daemon`, one per `CODEX_HOME`), which inherits the
environment of whichever session started it. When the hook's parent is that
daemon, it sends no `session_key` and no `agent_pid` (the daemon's pid), and
marks the event `hook_host: "daemon"`. The agent's own `session_id` (its
thread id, stable across daemon restarts, checked on 0.159.3) identifies the
session; the server resolves it (`core/native_identity.py`) and parks what it can't.
An id resolves through one DuckTerm recorded itself on the session: a
`NativeBound` event, or a server-published event that carries it. Ids from
hook events never count, because before this fix a daemon hook could carry
another session's. Only ids recorded since the session's last launch count, so a relaunch
(including a switch to another harness) clears the previous agent's id until
the new one binds; early events park and replay on the bind. An id two
sessions both recorded is ambiguous and parks.
A new id binds once per launch, when a `UserPromptSubmit` carries exactly one
live Codex session's launch prompt and that session has no bind since its
last launch (a server-published `SessionStart`). A prompt naming two
sessions, or a second id claiming an already-bound session, parks: every launch prompt names the session's
instruction file, under `sha256(session key)`, so no prompt or schema change
was needed. Anything else is parked: kept 10 minutes and replayed if its id
binds, counted at `GET /hooks/parked`, and never filed under a default
session. The daemon's pid is dropped. Every other `DUCKTERM_*`
variable the daemon holds belongs to another launch too: `DUCKTERM_URL`
(on 2026-10-01 a dead port), `DUCKTERM_HOME` and `DUCKTERM_INTERNAL`. So
under the daemon the hook ignores all of them and reports to the default
instance: `~/.duckterm/instance-url` (else port 4300) and the token in
`~/.duckterm`. A per-process agent still uses `DUCKTERM_URL` first.
Daemon-hosted Codex on a second instance is unsupported.
Probe P2 (0.159.3): the launch prompt comes back with the session's id in
its first `UserPromptSubmit`, and is also written to the rollout file. Design: "Design — Shared-daemon identity (Codex 0.159)", 2026-10-01.

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
