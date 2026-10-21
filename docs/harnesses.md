# Harnesses and skill kits

A **harness** is an agent CLI Duckterm runs (§1). A **skill kit** is a
collection of skills, hooks, sub-agents and guardrails those agents load (§2).

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

## 2. Skill kits — collections of skills, hooks, and sub-agents

A kit like [uv-suite](https://github.com/utsavanand/uv-suite) bundles skills,
hooks, sub-agents, guardrails, and personas. The **Skill kits** item in
Settings lists the kits you have added by folder path and shows what each
contains; the session panel's "Skill kit" row shows which kit a session's
folder has installed.

Duckterm does not install kits. It never runs a kit's own script (owner
decision 2026-10-04); installing returns with the declarative component
format, where the manifest lists components and Duckterm writes them per
harness. Until then, install a kit with its own instructions.

The manifest is `duckterm-harness.json` at the kit's root:

```json
{
  "name": "uv-suite",
  "description": "Agents, skills, hooks, guardrails, and personas for Claude Code",
  "detect": ".claude/skills/uv-help",
  "compatible": ["claude-code"]
}
```

- `detect` is a path, relative to a project, whose existence shows the kit
  is installed there.
- `compatible` lists the harnesses the kit works with.
- Older manifests' `install`, `uninstall` and `args_choices` keys are
  ignored. A folder without a manifest is refused.

Endpoints: `GET /harnesses`, `POST /harnesses/register {path}`,
`GET /harnesses/:name/contents`, `DELETE /harnesses/:name`. Registered kits
live in the `harnesses` table (name + path); the manifest is re-read from disk
on every use.

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
