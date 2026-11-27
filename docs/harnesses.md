# Harnesses and MetaHarnesses

A **harness** is an agent CLI Duckterm runs (§1). A **MetaHarness** (until 2026-11-25 a "MetaHarness") is a
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

## 2. MetaHarnesses — collections of skills, hooks, and sub-agents

A MetaHarness like [uv-suite](https://github.com/utsavanand/uv-suite) bundles skills,
hooks, sub-agents, guardrails, and personas. The **MetaHarnesses** item in
Settings lists the MetaHarnesses you have added by folder path and shows what each
contains; the session panel's "MetaHarness" row shows which MetaHarness a session's
folder has installed.

**The default MetaHarness.** DuckTerm ships one, `autopilot` ("Autopilot
default"), at `src/duckterm/autopilot/kits/autopilot`: two knowledge files
(working rules always on, danger zones on demand), seven skills (`spec`,
`plan`, `implement`, `test`, `review`, `qa`, `ship`), the permission rules a
coding session needs, four flows (`issue-to-merge` with its GitHub, Jira and
Slack triggers, `pr-review`, `build`, `ship`) and the `feature-team`
procedure (architect, dev, qa, release; only release pushes). No hook, no
tool, no model preset. Autopilot installs it into every sandbox run; it
declares `conflicts_with` the uv-suite MetaHarnesses it grew out of. Skills
write their files to `.autopilot/` when a person invokes them and to the path
the runner names when a flow does; `ship` never stages `.autopilot/`.

MetaHarnesses are behind the `skill_kits` development flag (the flag keeps its id): the Settings item,
the `/harnesses` routes and the "MetaHarness" row exist only on an instance that
has turned the flag on. See [feature-flags.md](feature-flags.md).

Installing never runs a MetaHarness's own script (owner decision 2026-10-04). A MetaHarness is
installed declaratively: the manifest lists components, and the installer
writes each one where the chosen harness reads it, or reports why it cannot.
The one place a MetaHarness script runs is a flow's `exec` step, at run time, and only
a script the install on this machine recorded (see Flows and the runner); a
MetaHarness's hooks are installed only at the MetaHarness digest reviewed at Enable. Both
pins are the owner's decision of 2026-11-22.
The format is the Skill MetaHarness Specification (`docs/specs/skill-kit-protocol-spec.md`
on the spec branch); `examples/metaharnesses/sample-kit` holds one of every
component kind and is the MetaHarness the installer is tried on.

The manifest is `metaharness.toml` at the root (`skill-kit.toml`, the name until 2026-11-25, is still read and validate asks for the rename; a direct path to any `*.toml` also works, as `metaharness.fast.toml` beside the main one):

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
legacy `duckterm-harness.json` loads (name, description, `detect`) but lists
no components, so the MetaHarnesses page refuses to add it and the CLI warns
about the retired name. A knowledge `load.paths` that names every file
(`**/*`) is `always` under another name and installs as such; a path scope
with `fallback = "always"` loads always for every harness as soon as one
chosen harness (Codex, OpenCode) cannot scope by path, because the fallback
text lives in the shared AGENTS.md.

The CLI is `duckterm metaharness` (`duckterm kit` stays as an alias: installed `/flow` shortcuts call it) and works from a bare clone, no server needed:

```
duckterm kit validate <kit>                      # authoring rules; runs no kit code
duckterm kit plan     <kit> --project . --harness claude-code --harness codex
duckterm kit install  <kit> --project . [--harness …] [--option persona=sport] [--replace]
duckterm kit verify   --project .                # every recorded file against its hash
duckterm kit uninstall <name> --project .        # exactly what the record says, nothing else
duckterm kit run <kit>/<flow> --project . [--harness claude-code|codex] [--input name=value]
duckterm kit run --resume <run-id> [--gate <step> approve|reject]
duckterm kit runs --project .                    # every run recorded here
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

Hooks go through a shim the installer writes beside the MetaHarness's scripts
(`_skill_kit_shim.py`): it rewrites each harness's payload to one neutral
shape, so a MetaHarness author writes one script, and maps a block decision back to
what that harness honours (exit 2, or a JSON decision for Copilot stop events).

Knowledge never replaces a hand-written `AGENTS.md` or `CLAUDE.md`: it is an
attributed block, or a rule in `.duckterm-rules.json` where the folder uses
typed rules. The record `.duckterm/installed.json` (committed) lists every
path written with its hash, and for every file the installer edited rather
than wrote (a settings JSON, `.codex/config.toml`, `AGENTS.md`, `CLAUDE.md`)
the bytes it found there and the hash of what it left. Uninstall puts the
original bytes back when the file is still what the install wrote; when the
owner changed it since, only the recorded MetaHarness additions come out and the
owner's edits stay. A copied MetaHarness file edited since install is kept and listed.
`verify` checks every entry: a deleted config is *missing*, one whose MetaHarness
values are gone is *drifted*; the owner's own additions beside them are
neither. Reinstalling repairs; it never duplicates a hook, rule or block.

The record comes with the repository, so a clone controls it. A copy of each
MetaHarness's entry is kept under the DuckTerm home, keyed by project path, and the
repository's entry is believed only when it matches that copy; otherwise
uninstall strips the text a MetaHarness of that name would have written (attributed
blocks, MetaHarness-owned rules, copied files that still hash), leaves config JSON as
it is and says so. Every path an install or uninstall touches, from the
record or from the adapters, is refused unless it is a path the adapters write
(never `.git/`), relative, with no symlink anywhere on the way (a committed
`.claude -> .git` would otherwise carry an allowed name into `.git/`), and
still inside the project. Links are refused, not followed, even when they
resolve inside the project: a project whose agent folder is a link (say
`.claude/skills -> ../shared`) cannot use MetaHarnesses until the link is replaced
with a real folder (owner decision, 2026-11-21). Component names are one path
segment (`[a-z0-9_-]`),
and a MetaHarness's own sources must be real files under the MetaHarness root; a symlink
anywhere on the way fails `validate`. Uninstall prunes only the directories
the install created.

**Flows and the runner.** `duckterm kit run <kit>/<flow>` runs an installed
flow (spec §3.7, Part III). Install writes the flow file under
`.duckterm/flows/<kit>/`, a `skills.json` beside it with every skill's declared
outputs and effects and the hash of every script a flow `exec`s, and those
scripts. Each `run`/`decide` step is a fresh non-interactive harness process
(`claude -p --output-format json --json-schema …`, or `codex exec
--output-schema …`); the runner asks for a typed result and validates it
itself, retrying an invalid one twice with the error. A step is judged by its
exit code first, never by the harness's own status field. `gate` pauses the
run (exit 3) until `--resume … --gate <step> <answer>`; `parallel` runs
read-only steps together; `wait` polls CI or review on the branch head through
`gh` until pass, fail or its timeout; `exec` runs a MetaHarness script with the step's
inputs as JSON on stdin and reads a typed result from its last stdout line.
`next` sends a step somewhere other than the next in the list; any edge
pointing backwards carries `max_loops`. Copilot and OpenCode steps are refused
with the reason; Codex steps of a MetaHarness with hooks need `--codex-trusted`.

What the runner believes. The flow file, `skills.json` and the scripts sit in
the repository, so a clone or a pulled commit can rewrite all three together.
The runner reads them only when each still hashes as the install record under
`DUCKTERM_HOME/kits/records` says this machine wrote it; a project never
installed here is refused with "run `duckterm kit install`", which shows the
plan first. A flow named by path (`duckterm kit run path/to/flow.json`) is a
local trial: its harness steps run, its `exec` steps are refused. A gate
re-hashes what it showed when the answer arrives; a shown file that changed in
between ends the run (`gate_shown_changed`), because a step's output was
altered outside the runner. A step reading a file output (`{path, sha256}`)
checks the hash again before it starts. The run file itself is in the
repository too, so a commit can carry one already past its gate: this machine
notes the hash of every run file it writes (`<home>/MetaHarnesses/runs/<project
key>/<run id>`), a run with no note "was not started on this machine", one
whose file no longer matches "changed outside the runner", and `runs` marks
such runs `recorded: false` so the Flows tab lists them without a gate or
resume. The run file and its note are written, and read, under one lock
per project (`<home>/kits/runs/<project key>/.lock`), so a load during a
save never pairs a new run file with the previous note. A gate showing `workspace.diff` sees the worktree against HEAD with
new files included (a temporary index after `git add -A`, the runs folder
excluded) and records the worktree's tree id; the answer re-checks it and the
next step checks it once more, so a worktree that changed after the owner
looked fails the run (`workspace_changed`). Step ids match `[a-z0-9_-]{1,64}`
(they name folders under `.duckterm/runs/`), run and attempt folders are
refused through a symlink or outside the project, an `exec` file output must
be a file under its own attempt folder, and a run file that names a flow by
path does not resume.

Run state is `.duckterm/runs/<run-id>/run.json`: `run_id`, `flow`
(`<kit>/<name>`), `flow_hash`, `kit`, `harness`, `inputs`, `executor` (`local`;
autopilot adds `executor_state`), `state` (`running` · `waiting_gate` ·
`succeeded` · `failed`), `current` {step, attempt}, `steps_taken`, `loops`
(back-edges taken per step), `attempts` (one record per attempt: step, attempt,
kind, harness, status `started` · `completed` · `invalid` · `failed` ·
`abandoned`, started_at, finished_at, exit, inputs_sha256, choice, reason,
outputs {name: value | {path, sha256}}), `gates` (step, visit, question, show,
shown_sha256, choices, asked_at, response) and `failure` {step, attempt,
reason}. Each attempt owns `.duckterm/runs/<run-id>/<step>/<attempt>/`, where
its file outputs and `workspace.diff` land; a stale file elsewhere is never
taken for this attempt's work. A run holds a `lock` file while it is driven,
is written atomically after every change, and resumes only at a step
boundary: a started attempt without a receipt is rerun when read-only,
and ends the run as `outcome_unknown` when it changed the workspace or acted
outside it. A flow file that changed since the run started refuses to resume.

The executor seam (`runner.Executor`): `run_process(argv, cwd, env, stdin,
timeout)` runs the step's process (locally a subprocess in the project;
autopilot wraps the same argv in `docker exec` with its proxy env),
`after_step(run, attempt)` runs after a valid attempt and before its receipt
is written (autopilot commits and pushes the run branch there, so a receipt
always means the work is where the next attempt will find it), `check`
answers `wait` steps, and `harness_ready(harness, project, hooks, limits)`
says why a harness cannot run a step, or None: the host answers for a local
run (binary on PATH, Claude Code workspace trust, `--codex-trusted`), a
sandbox executor answers from inside the sandbox, where the harness and its
trust record actually live. The runner itself checks only that the step's
skill is installed in the project.

**The MetaHarnesses page** (Settings → MetaHarnesses, behind the flag) is a front
end to the same engine. It lists the MetaHarnesses installed in a workspace's
repository with their verify state and flows, and the procedures they ship
(from the install record, which carries each team template's roles and
rules); Add takes a MetaHarness folder, shows the inventory and options, then the
review page is the plan: one row per component, one status per harness, the
conflicts with keep or replace, the files. A conflict is hard (`conflicts-with`,
`name-clash`, `other-kit`, `escapes`: the install refuses), a file the owner
placed by hand (`file-exists`: keep or replace), or an **overlap**: another
MetaHarness installed in the same repository does the same job under another
name, judged from its manifest at the recorded source: two skills that produce
the same named output (`spec`, `plan`, `verdict`) or two flows started by the
same trigger. Both install; the plan, the CLI and the review page say so and
suggest enabling one of them there. Install, verify and uninstall call
the engine; Run starts a flow in a server thread and the page watches the run
file. **The page starts from MetaHarnesses, not repositories.** Add MetaHarness takes a
folder or a manifest path, reads the manifest and adds the MetaHarness to
DuckTerm (`POST /kits/register`; the record is the `harnesses` table, name
and path). The list (`POST /kits/catalog`) shows every added MetaHarness with what it
holds and the workspaces it is installed in. A workspace's repositories are
the git repositories its sessions run in directly, asked of git on every
load; a linked worktree counts for nothing there, because its main checkout
may be where other workspaces' sessions run, hooks included, and the
worktree gets the MetaHarness at its next session start; a plain folder or a
deleted one counts for nothing either, so a workspace with no repository
yet gets the MetaHarness when a session starts in one. Adding is
three steps: pick the MetaHarness (Browse marks MetaHarness folders and offers each MetaHarness of a
multi-MetaHarness folder), read what it holds, enable it for all workspaces or chosen
ones (`POST /kits/enable`; the choice lives in `<home>/kit-policy.json`).
The chooser lists workspaces only, no folders: enabling is a workspace
property. The review (`POST /kits/review`) is one matrix for the MetaHarness, since
what each harness gets depends on the harness, not the folder: components in
sections by kind, collapsed, a section with something a harness cannot take
marked; above it, one strip with everything per repository that differs
and can be put right from there, each with a Fix: Codex trust (hooks run
only in a repository Codex trusts; `POST /kits/fix` with `codex-trust`
writes `trust_level = "trusted"` for the repository into `~/.codex/config.toml`,
the record Codex itself writes when the owner accepts its prompt, and one
click covers every MetaHarness and session there; owner decision 2026-11-25, the
only place DuckTerm writes trust, never at install or SessionStart), a
knowledge file with a path scope on a harness without one (`fallbacks`:
load it always there, kept with the policy so later SessionStart installs
do the same), a file that exists and is not from a MetaHarness (replace). A MetaHarness's
permission rules reach Copilot as `--allow-tool`/`--deny-tool` flags when
DuckTerm launches a Copilot session in the repository: the flags are
computed at install from the tree the owner reviewed and kept in the
home-side install record with the MetaHarness's digest; the launcher reads only
that record, never the MetaHarness's current files or the repository's own copy
of the record, and passes a MetaHarness's flags only while its folder still
hashes the same (a changed MetaHarness's flags are dropped and logged until Enable
reviews it again). A permission `match` may not contain `)`, `,` or a
newline, so one rule cannot read as two. OpenCode hooks (JS plugins)
have no fix yet. Enabling installs into every matching repository known
now, for every harness, with the options and fixes the review showed; the
policy keeps them (`options`, `fallbacks`) for the installs SessionStart
does later. Enable pins a digest of the MetaHarness tree
the review showed (`digest` in the policy). On a SessionStart the server
installs enabled MetaHarnesses the session's repository lacks, but only that pinned
tree: a MetaHarness folder that changed since (a pull, an edit under ~/Downloads)
installs nowhere, the page shows it as "changed since enabled", and Enable
again is the review of the new tree. The install target is the repository
DuckTerm launched the session in (the session row's worktree or repository
as it stood before the event, a git root, never the home folder), and only
when the event's cwd lies inside it; the hook's own cwd is never a target,
because a `claude` an agent starts in another folder inherits the session
key and reports that folder. Create team works from an added MetaHarness: the new
workspace is enabled for the MetaHarness (pinned to the tree the MetaHarness page showed),
so its first session installs it. `kit-policy.json` is written atomically
and never through a symlink. A MetaHarness's name is unique in the registry;
registering a second MetaHarness or suite of that name is refused.

The server routes are `POST /MetaHarnesses/catalog | register | unregister | enable |
disable | overview
| plan | install | uninstall | verify | run | runs | run-detail | gate | teams
| team` (`duckterm.MetaHarnesses.api`); the ones that touch a repository take its
path in the body. **Create team** (`POST /MetaHarnesses/team
{project, MetaHarness, team, name?}`) makes a workspace carrying the MetaHarness's procedure:
the record (roles and rules copied from the installed MetaHarness's template, with
MetaHarness and version) is `<home>/teams/<name>.json`, the workspace is registered
like New workspace, and the workspace view shows one card per role.

**A run shows in the app while it runs.** The session's Flows tab lists the
repository's runs, the session's own first, each with its steps and, at a
gate, Approve and Reject under the step. A run the server starts for a
session (`POST /api/v1/session/kits/run`, authenticated by the session's own
credential; also `runs` and `run-detail`) makes every harness step a child
session of it: a SessionStart row with the parent key before the process,
the step's hooks reporting under the child key, SessionEnd after. A session
credential reaches only the session's own folder (the body cannot name
another project), sees only the runs it started, and answers no gate: gates
are the owner's, in the Flows tab, so there is no session gate route.
Neither HTTP route takes `yolo` or `codex_trusted`; the harness keeps its
own permission prompts unless the owner passes `--yolo` or `--codex-trusted`
to `duckterm kit run` in a terminal outside a session, and flows run by
name only (`<kit>/<flow>`, installed), never by file path. `duckterm MetaHarness
run` inside a session hands the run to the server this way and watches it
from the terminal; outside a session it runs locally. The `/<flow>` command
a flow installs for Claude Code pre-approves exactly `duckterm MetaHarness run
<MetaHarness>/<flow>` (no `--gate`, no `--yolo`), with the flow's description as one
quoted frontmatter line and only identifier input names; a flow without
inputs pre-approves the exact command, no prefix. Inside a session (any
`DUCKTERM_SESSION_KEY`) the CLI refuses `--yolo`, `--codex-trusted` and
`--gate` before it looks anything up, and a missing session credential is an
error, never a fall-back to the local runner. Script steps are not sessions.

Where a session lives, for MetaHarnesses, is the folder DuckTerm launched it in:
the launcher writes `<home>/kits/launches/<session>.json` and nothing else
does. The session row's `cwd` and `repo_path` follow every hook event, so a
`claude` an agent starts in another repository moves them; the session MetaHarness
routes and the SessionStart install never read them. A session with no
launch record (one DuckTerm did not start) gets 409 from the session MetaHarness
routes and no auto-install.

Endpoints: `GET /harnesses`, `POST /harnesses/register {path}`,
`GET /harnesses/:name/contents`, `DELETE /harnesses/:name`. Registered MetaHarnesses
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
