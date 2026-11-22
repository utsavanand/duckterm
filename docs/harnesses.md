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

Skill kits are behind the `skill_kits` development flag: the Settings item,
the `/harnesses` routes and the "Skill kit" row exist only on an instance that
has turned the flag on. See [feature-flags.md](feature-flags.md).

Duckterm never runs a kit's own script (owner decision 2026-10-04). A kit is
installed declaratively: the manifest lists components, and the installer
writes each one where the chosen harness reads it, or reports why it cannot.
The format is the Skill Kit Specification (`docs/specs/skill-kit-protocol-spec.md`
on the spec branch); `examples/skill-kits/sample-kit` holds one of every
component kind and is the kit the installer is tried on.

The manifest is `skill-kit.toml` at the kit's root:

```toml
manifest_version = 1
name        = "sample-kit"
description = "One of every component kind"
version     = "0.1.0"

[[components]]
kind = "skill"
path = "skills/raise-pr"

[[components]]
kind = "hook"
event = "before_tool"
match = "shell"
command = "hooks/block-destructive.sh"
blocking = true
```

Seven component kinds: `knowledge`, `tool`, `skill`, `agent`, `hook`,
`permission`, `flow`, plus `settings` as the per-harness escape hatch. A
legacy `duckterm-harness.json` still registers (name, description, `detect`)
but lists no components and warns about the retired name.

The CLI works from a bare clone, no server needed:

```
duckterm kit validate <kit>                      # authoring rules; runs no kit code
duckterm kit plan     <kit> --project . --harness claude-code --harness codex
duckterm kit install  <kit> --project . [--harness …] [--option persona=sport] [--replace]
duckterm kit verify   --project .                # every recorded file against its hash
duckterm kit uninstall <name> --project .        # exactly what the record says, nothing else
```

`plan` prints one row per component and one status per harness: *installed*,
*installed, needs trust* (Codex project hooks), *not supported* with the
reason (OpenCode hooks, Copilot permissions, Codex read/write rules),
*installed, not yet verified* (OpenCode, docs only), *not applicable* (an
option not chosen). Where files land per harness is the adapter table in
`src/duckterm/kits/adapters.py`:

| | Claude Code | Codex | Copilot | OpenCode |
|---|---|---|---|---|
| knowledge, always | `AGENTS.md` block + `@AGENTS.md` import in `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` (loads twice with `CLAUDE.md`) | `AGENTS.md` |
| knowledge, paths | `.claude/rules/<kit>-<name>.md` | not supported | `.github/instructions/…` | not supported |
| skills | `.claude/skills` | `.agents/skills` | reads both | reads both |
| agents | `.claude/agents/*.md` | `.codex/agents/*.toml` | reads `.claude/agents` | `.opencode/agents/*.md` |
| hooks | `.claude/settings.json` | `.codex/hooks.json`, needs trust | `.github/hooks/<kit>.json` | not supported |
| permissions | `permissions` in settings | `.codex/rules/<kit>.rules` (shell only) | flags only | `opencode.json` |
| MCP tools | `.mcp.json` | `.codex/config.toml` | `.mcp.json` | `opencode.json` |

Hooks go through a shim the installer writes beside the kit's scripts
(`_skill_kit_shim.py`): it rewrites each harness's payload to one neutral
shape, so a kit author writes one script, and maps a block decision back to
what that harness honours (exit 2, or a JSON decision for Copilot stop events).

Knowledge never replaces a hand-written `AGENTS.md` or `CLAUDE.md`: it is an
attributed block, or a rule in `.duckterm-rules.json` where the folder uses
typed rules. The record `.duckterm/installed.json` (committed) lists every
path written with its hash, and for every file the installer edited rather
than wrote (a settings JSON, `.codex/config.toml`, `AGENTS.md`, `CLAUDE.md`)
the bytes it found there and the hash of what it left. Uninstall puts the
original bytes back when the file is still what the install wrote; when the
owner changed it since, only the recorded kit additions come out and the
owner's edits stay. A copied kit file edited since install is kept and listed.
`verify` checks every entry: a deleted config is *missing*, one whose kit
values are gone is *drifted*; the owner's own additions beside them are
neither. Reinstalling repairs; it never duplicates a hook, rule or block.

The record comes with the repository, so a clone controls it. A copy of each
kit's entry is kept under the DuckTerm home, keyed by project path, and the
repository's entry is believed only when it matches that copy; otherwise
uninstall strips the text a kit of that name would have written (attributed
blocks, kit-owned rules, copied files that still hash), leaves config JSON as
it is and says so. Every path an install or uninstall touches, from the
record or from the adapters, is refused unless it is a path the adapters write
(never `.git/`), relative, with no symlink anywhere on the way (a committed
`.claude -> .git` would otherwise carry an allowed name into `.git/`), and
still inside the project. Links are refused, not followed, even when they
resolve inside the project: a project whose agent folder is a link (say
`.claude/skills -> ../shared`) cannot use skill kits until the link is replaced
with a real folder (owner decision, 2026-11-21). Component names are one path
segment (`[a-z0-9_-]`),
and a kit's own sources must be real files under the kit root; a symlink
anywhere on the way fails `validate`. Uninstall prunes only the directories
the install created.

The dashboard's **Skill kits** dialog still only lists kits by folder path and
shows their contents; the install wizard on top of `plan`/`install` is next.

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
