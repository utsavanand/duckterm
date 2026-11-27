# DuckTerm support guide

This guide is the knowledge base for DuckTerm's in-app Support chat. Answer from
this guide and the live diagnostics supplied with the question (version,
edition, enabled features, `duckterm doctor` results). If the guide does not
cover something, say so and suggest **Report a bug** (section 8). Never invent
commands, flags, menu items or file paths.

## 1. What DuckTerm is

DuckTerm is a multi-agent orchestrator. It launches coding agents (Claude Code,
Codex, GitHub Copilot CLI, or any other command) into terminals it owns, and
shows them in one dashboard: a Mac app window or a browser tab.

Terms:

- **Harness**: an agent CLI DuckTerm runs. Supported: `claude-code`, `codex`,
  `copilot`, plus `generic` for any other command (driven in a terminal, no
  hook-reported state).
- **Session**: one agent running in one DuckTerm terminal.
- **Workspace**: a top-level group in the sidebar (**New → New workspace**).
  Older docs, some CLI flags (`--folder`) and API fields call it a "folder".
  Sessions in the same workspace can message each other.
- **Skill kit**: an installable collection of skills, hooks, sub-agents,
  instructions and flows that agents load.
- **Oracle**: DuckTerm's fleet monitor (Ask Oracle chat and idle-inbox
  reminders). It is not an agent session.

Your agents keep their own logins, subscriptions and tools. DuckTerm does not
replace the agent's sign-in.

### Editions

Three editions. Each Mac app runs one edition. Features are cumulative.

| Edition | Includes |
|---|---|
| **Lite** (public download) | Workspaces and workstreams, coding-agent sessions, terminals, Shell, Messages view, forks, session status (busy / waiting / idle), session inbox, checkpoints and summaries, context usage, handoff, artifacts, control tower overview |
| **Code** | Everything in Lite, plus file explorer and file editing, Git changes, workspace (folder) chat, connectors, PR review, AGENTS.md rule suggestions |
| **Full** | Everything in Code, plus Oracle (Ask Oracle, idle-inbox reminders, voice) and remote workspaces |

Lite is downloaded from GitHub Releases. Code and Full are available through
the edition download forms on the DuckTerm website.

Some features are switched off in every edition until turned on locally; see
section 4.13 (feature flags). The live diagnostics list which features this
installation has enabled; trust that list over this table.

## 2. Install, update, restart, uninstall

### Requirements

- Mac app: Apple Silicon, macOS 13 or later. Python, tmux, jq, the backend and
  the dashboard are bundled. No Homebrew, pipx or separate backend needed.
- Browser/CLI install (Intel Mac, Linux): Python 3.11+, pipx, tmux.
- Windows: WSL 2 plus a Windows browser. Untested.
- Install and sign in to each agent CLI yourself first (run it once directly).
- Git is needed for Git workstreams (worktrees, forks).

### Install the Mac app

1. Download `DuckTerm-Lite-macos-arm64.dmg` from
   https://github.com/utsavanand/duckterm/releases/latest
2. Open the DMG and drag **DuckTerm Lite.app** to **Applications**.
3. Open the app **from Applications** (not from the DMG or Downloads).

The app starts its backend automatically, or connects to an already running
server of the same edition. If another edition is running, the app explains
how to switch; it does not stop that server.

### Install the browser/CLI version

macOS with Homebrew:

```sh
brew install pipx tmux
pipx ensurepath
```

Open a new terminal, then install the wheel from GitHub Releases (replace
VERSION with the release, e.g. `0.4.216`):

```sh
pipx install https://github.com/utsavanand/duckterm/releases/download/vVERSION/duckterm-VERSION-py3-none-any.whl
duckterm serve --edition lite
```

DuckTerm is not on PyPI. `pipx install duckterm` fails with "No matching
distribution"; install the GitHub release wheel URL instead.

`duckterm serve` keeps running in that terminal and opens the dashboard at
http://127.0.0.1:4300. Leave the terminal open.

### Using the CLI with an app-only install

The app ships its own CLI. Use the full path in place of `duckterm`:

```sh
"/Applications/DuckTerm Lite.app/Contents/Resources/bin/duckterm" doctor
```

For Code or Full, replace `DuckTerm Lite.app` with that app's name. Inside
terminals that DuckTerm launches, `duckterm` is already on PATH. The app's CLI
does not replace a separately installed pipx CLI, and the two are updated
separately.

### Hooks (needed for live session state)

Install hooks for each agent you use **before launching new sessions**:

```sh
duckterm install-hooks --agent claude-code --global
duckterm install-hooks --agent codex --global
duckterm install-hooks --agent copilot --global
```

- `--global` writes the user-level agent config, applying to every project.
  Without `--global`, hooks are written into the current repository only.
  For Codex, always use `--global`.
- Files written: Claude Code `~/.claude/settings.json`; Codex
  `~/.codex/hooks.json`; Copilot `~/.copilot/hooks/duckterm.json`.
- Codex: after installing, start `codex`, run `/hooks`, and trust the DuckTerm
  hook. Repeat if the hook script changes.
- Codex permission prompts must be answered in the Codex terminal; they do not
  appear as dashboard buttons.
- Already-running sessions pick up new hooks only after they restart.

Then run `duckterm doctor`.

### Codex version

DuckTerm is verified with Codex 0.155.x. Codex 0.159 and later are **not
supported yet**: they run one shared daemon for all Codex sessions, so sessions
show the wrong state and messages, and questions Codex asks can be lost.

Check with `codex --version`. To stop Codex upgrading itself at startup, add
this as a top-level line in `~/.codex/config.toml`, above any `[section]`:

```toml
check_for_update_on_startup = false
```

Codex has a background updater this may not cover; recheck the version after
Codex restarts. To switch back to an installed older version:

```sh
ln -sfn ~/.codex/packages/standalone/releases/0.155.1-aarch64-apple-darwin \
  ~/.codex/packages/standalone/current
codex --version
```

(Use whichever 0.155.x folder exists under `~/.codex/packages/standalone/releases/`.)

### Update

Mac app:

- **Settings → Update DuckTerm** in the dashboard checks GitHub for a newer
  release. On an updater-enabled app, choose **Update & restart**; it updates
  the whole app (window, backend, CLI, dashboard, tmux). Running agent
  terminals survive. It does not update agent CLIs or a separate pipx install.
- If the card only offers **Download update**, quit the app, replace it in
  Applications with the new download, and reopen it.
- If updating fails because the app is not in Applications (macOS ran it from a
  temporary location), quit, move the app to Applications, and reopen it.
- If you also run a separate `duckterm serve` terminal, stop it with Ctrl-C
  before reopening the app.

Browser/CLI:

```sh
pipx install --force /absolute/path/to/duckterm-VERSION-py3-none-any.whl
```

Stop the running server with Ctrl-C, start it again with `duckterm serve`
(keeps the saved edition), and reload open browser tabs.

Downgrades: an older release refuses a database written by a newer one (see
"database is schema vN" in section 6). Back up first if you can (section 4.12).

### Restart

- Mac app: quit and reopen the app.
- CLI: `duckterm restart` stops the running server for this data directory and
  starts a fresh one in the current terminal. Agent terminals (tmux) keep
  running and are re-attached. Options: `--edition lite|code|full`, `--port`.
- `duckterm restart` refuses to stop a server it cannot prove belongs to this
  data directory ("Cannot verify this workspace owns the server … left it
  running").

After a **Mac reboot**, agent processes are gone. Their sessions show
**Interrupted**. Right-click a session and choose **Resume** to relaunch it
(Claude Code continues the conversation). DuckTerm does not restart them
automatically.

### Uninstall

1. Remove hooks first, for each agent you configured:
   ```sh
   duckterm uninstall-hooks --agent claude-code --global
   duckterm uninstall-hooks --agent codex --global
   ```
   (Use the app's CLI path for an app-only install.)
2. `duckterm uninstall` previews what cleanup would touch without changing
   anything. `--project PATH` also inspects that project's hooks (repeatable).
3. CLI: `pipx uninstall duckterm`. App: delete it from Applications.

Data in the DuckTerm home (below) is kept. Delete it yourself only if you want
your sessions, history and settings gone.

### Where data lives

| Install | Data directory (DuckTerm home) | Dashboard port |
|---|---|---|
| DuckTerm Lite app | `~/.duckterm-lite` | 4519 |
| DuckTerm Code app | `~/.duckterm-code` | 4800 |
| DuckTerm (Full) app | `~/.duckterm` | 4300 |
| `duckterm serve` (CLI) | `~/.duckterm` | 4300 |

- `DUCKTERM_HOME` overrides the data directory; `DUCKTERM_PORT` the port.
- `DUCKTERM_INSTANCE=name` gives a separate data directory
  (`~/.duckterm-name`), tmux socket and a port in 4310–4909.
- The server only binds 127.0.0.1 / localhost.

Contents of the home that matter for support:

| Path | What it is |
|---|---|
| `db.sqlite` (+ `-wal`, `-shm`) | Sessions, workspaces, history, inbox, checkpoint metadata. Never copy `db.sqlite` alone while the server runs; use `duckterm backup`. |
| `token` | Owner token; created on first `duckterm serve`. Hooks read it. |
| `edition.json` | The edition last started here. |
| `flags.json`, `feature-flags.json` | Local feature flags (section 4.13). |
| `session-credentials/` | One private credential per session (secrets). |
| `checkpoints/`, `snapshots/` | Checkpoint and snapshot data. |
| `worktrees/` | Git worktrees for isolated sessions. |
| `backups/` | Default `duckterm backup` destination. |
| `oracle-chat.json` | Ask Oracle conversation (last 200 exchanges). |
| `bug-reports/` | Bug report ZIPs prepared from the dashboard. |

Agent conversation transcripts are not in DuckTerm's home. They stay in the
agent's own directories (`~/.claude/projects/`, `~/.codex/`). Forks, Messages,
context usage and resume depend on them.

## 3. How it works (for troubleshooting)

- **Server**: one local Python process (standard library only) serving the
  dashboard and API on 127.0.0.1 at the port above. State is in SQLite in the
  DuckTerm home.
- **Dashboard**: a web app shown in the Mac app's window or in a browser.
  Terminals are xterm.js connected to the server over a WebSocket.
- **Sessions**: each agent runs in a tmux pane owned by DuckTerm (a private
  tmux socket per edition/instance). Because of tmux, agents keep running when
  the server or app restarts. A reboot ends them.
- **Hooks**: `duckterm install-hooks` adds a hook to the agent's own config.
  The agent calls it on session start, tool use, sub-agent start/stop,
  permission requests and turn end. The hook posts these events to the server
  using `curl` (and `jq` for the approval path). Session state badges
  (busy / waiting / idle), approval buttons and the sub-agent tree come from
  these events. No hooks, or untrusted hooks, means no live state.
- **Context usage** (tokens used / left) is read from the agent's transcript
  file on disk.
- **Security basics**: dashboard reads are limited to this machine; changes
  need the owner token. Each session gets its own credential that only lets it
  use its own inbox and artifacts. Agents run as your OS user, so DuckTerm does
  not sandbox them from each other.

## 4. Using DuckTerm

### 4.1 Sessions

- **Start**: **New → New session**, choose an installed agent and a project
  directory, then type your task in its terminal. Options include an
  **Isolated worktree** (a separate Git checkout per attempt) and, in Full with
  remote enabled, **Run on → This Mac / Remote**.
- **From a terminal**:
  - `duckterm run --name login claude` runs the agent in the current terminal
    as a DuckTerm session. Arguments after the agent name go to the agent.
    It warns if that agent's hooks are not installed.
  - `duckterm launch 'claude' --cwd PATH --prompt TEXT` starts a session inside
    the running server.
- **States**: Busy, Idle, Waiting on you (needs an answer or approval),
  Stopped, Interrupted (terminal ended, e.g. after reboot), Ended.
- **Stop** pauses; **Resume** relaunches (continuing the conversation for
  Claude Code). **Archive** is final. **Delete** needs a second click and also
  cancels that session's inbox work and deletes its artifacts.
- **Fork**: copy a session's Git state, or (Claude Code) its conversation, into
  a new session.
- **Messages** tab: the conversation as formatted text. Select text in a reply
  and add a note to send it back to the agent as a follow-up. Pin messages to
  revisit them.
- **Shell** tab: a plain shell in the project for your own commands.
- **Approvals**: Claude Code and Copilot permission prompts appear as buttons
  in the dashboard. Codex prompts must be answered in its terminal.
- Terminal: 5000 lines of scrollback; six color themes (global, per workspace
  or per session). In the Mac app, hold **Option** while dragging to select
  text inside an agent that captures the mouse, then **Command-C**.

### 4.2 Workspaces and the sidebar

- **New → New workspace** creates a sidebar group. Drag sessions into it.
  Groups nest.
- Opening a workspace shows its sessions as a **grid** of terminals: drag a
  pane onto another's edge to split, drag resize bars, collapse panes to a dock.
- Settings → Sidebar density: Compact / Standard / Relaxed.
- Workspace membership controls collaboration: sessions in the same top-level
  workspace can discover and message each other. Ungrouped sessions are private.

### 4.3 Session inbox and collaboration (`duckterm session …`)

Agents in the same workspace can send each other questions and assignments.
These commands only work inside a DuckTerm session terminal (they need the
session's credential). Outside one, they fail with
"DUCKTERM_SESSION_KEY is missing; run this inside a Duckterm session".

| Command | What it does |
|---|---|
| `duckterm session self` | Show this session's card |
| `duckterm session discover [--scope self_folder\|parent\|grandparent\|shared_root]` | List sessions this one may message |
| `duckterm session publish --purpose TEXT --activity TEXT` | Update what this session says it is doing |
| `duckterm session ask TARGET "QUESTION" [--timeout SECONDS]` | Send a question; prints a request ID. TARGET is a session ID, peer reference, or a unique visible session name |
| `duckterm session get REQUEST_ID` | Check a sent request's status and answer |
| `duckterm session inbox` | Read pending questions for this session |
| `duckterm session accept REQUEST_ID` | Accept a request |
| `duckterm session reply REQUEST_ID --file ANSWER.md` | Answer (reads stdin without `--file`) |
| `duckterm session decline REQUEST_ID [--file FILE]` | Decline |
| `duckterm session cancel REQUEST_ID` | Cancel a request you sent |
| `duckterm session task start TITLE` / `list` / `update ID --status in_progress\|done\|parked --note TEXT` / `handoff ID TARGET` | Report work explicitly |

Behavior:

- Requests have no deadline by default; they stay until answered, declined or
  cancelled. `--timeout` sets one (up to 7 days). Closed records are cleaned up
  after 7 days.
- If a name matches more than one session, use the ID from `discover`.
- `session ask` showing "delivered" means it reached the recipient's inbox, not
  that the agent read or accepted it. Check with `session get`.
- Stop/Resume keeps pending requests. Deleting a session cancels its work.
- **Turn-end reminders**: Claude Code sessions get a one-line reminder of
  accepted, unanswered work and unread owner broadcasts when a turn ends.
  Codex, Copilot and generic sessions get no automatic reminder.
- New sessions are told to check their inbox. For a session started before
  collaboration was available, use **Introduce session collaboration** in that
  session's Inbox tab.
- **Owner broadcasts**: send a message to every agent in a workspace. Kept for
  7 days. A priority broadcast is repeated at every turn end until the agent
  replies to it.

### 4.4 Oracle (Full edition)

- **Ask Oracle**: a chat that answers questions about all running sessions at
  once ("who is stuck?", "what has X done?"). Each question is one model call
  over a digest of every running session. Uses the backend chosen in
  Settings → AI models (section 4.14).
- **Idle-inbox reminders**: once a minute Oracle checks idle agents with
  waiting mail and types one fixed reminder line into the agent, telling it to
  run `duckterm session inbox`. It only does this when all of these hold:
  - the session is idle (not waiting on you) for 5+ minutes since its last turn;
  - nobody typed in that terminal in the last 2 minutes;
  - the agent's input box is empty on screen;
  - the mail is an owner broadcast, accepted work, or a peer question unread
    for 5+ minutes;
  - there is new mail since the last reminder (otherwise it waits an hour).
  Priority owner broadcasts skip the 5-minute wait. The reminder never quotes
  the mail.
- Works for Claude Code, Codex and Copilot CLI (prompt detection). Not for
  generic sessions, and not for sessions running without tmux.
- Turn off all Oracle activity by starting the server with
  `DUCKTERM_ORACLE=off` in its environment.
- **Voice**: Settings → Voice announcements speaks when an agent needs you or
  finishes, while a dashboard is open. `duckterm voice status|install|remove`
  manages the local natural voice (Apple Silicon).

### 4.5 Control tower

The control tower page shows the whole fleet: tiles for Agents (busy / waiting
/ idle), Needs you, Tokens (last 7 days, Claude Code and Codex), Agent mail
(24h), Last backup and Remote sessions; each agent drawn as a duck grouped by
workspace (hover for name and current work); and a way to message a session
directly. In Full, the Ask Oracle chat is in its right rail. **← Sessions**
returns to the terminals.

### 4.6 Checkpoints and memory

A checkpoint summarizes a session's conversation so its context can be carried
forward (handoff, resume, switching harness).

- **Checkpoint** button in the session panel's memory section. The dialog
  counts unprocessed conversation records (single user or assistant messages,
  not turns) and asks how many to summarize (1–1,000). Shows **Retry
  checkpoint** after a failure.
- **Settings → Checkpointing**: default number of records, the summary writer
  model for Claude Code (Haiku, Sonnet, Opus), and **Automatic checkpoints**
  (off by default).
- Checkpoints need a model backend (section 4.14).
- Agents can search their own history:
  `duckterm memory search QUERY`, `duckterm memory read SOURCE`,
  `duckterm memory items [--category intent|rules|facts]`,
  `duckterm memory sources`, `duckterm memory sessions`.
- `duckterm snapshot` bundles recently active sessions to disk.

### 4.7 Artifacts

Agents register deliverables (reports, specs, previews) so you can read them
in the **Artifacts** tab next to Inbox.

```sh
duckterm session artifact /absolute/path/report.md --title "Release review"
duckterm session artifacts
duckterm session artifact get ARTIFACT_ID --output /path/that/does/not/exist
```

- DuckTerm stores a copy; re-registering the same path updates it. The source
  path is not a live link. No version history.
- Limits: 5 MiB per file, 200 files / 100 MiB per session, 500 MiB total.
  Symlinks and non-regular files are rejected. Text must be UTF-8.
- Previews: Markdown, HTML (static; scripts and network disabled), text,
  PNG/JPEG/GIF/WebP/SVG, PDF. Other types are downloadable.
- Highlight text in a preview (or use **Feedback**) to send a comment back to
  the producing session as a follow-up prompt. A stopped agent or a changed
  artifact returns an error and keeps your comment.
- Agents register cooperatively; DuckTerm does not scan for files.

### 4.8 Skill kits (feature flag `skill_kits`)

A skill kit is a folder with a `skill-kit.toml` manifest listing skills,
hooks, sub-agents, knowledge, permissions, MCP tools and flows.

- **Settings → Skill kits**: **Add** a kit folder, review what it contains,
  then enable it for all workspaces or chosen ones. Enabling shows a dry-run
  plan per harness first, and names components a harness does not support.
- CLI (works without a server):
  ```sh
  duckterm kit validate KIT
  duckterm kit plan KIT --project . --harness claude-code --harness codex
  duckterm kit install KIT --project .
  duckterm kit verify --project .
  duckterm kit uninstall NAME --project .
  duckterm kit run KIT/FLOW --project .
  duckterm kit runs --project .
  ```
- Installing never runs kit scripts. The record of every file written is
  `.duckterm/installed.json` in the project. `verify` exits 1 on drift;
  reinstalling repairs. Uninstall removes only what the kit wrote and keeps
  your own edits.
- Projects whose agent folders are symlinks (e.g. `.claude/skills -> ../x`)
  cannot use skill kits; replace the link with a real folder.
- `kit run` exits 3 while a flow waits at a gate; resume with
  `duckterm kit run --resume RUN_ID --gate STEP approve|reject`.
- Kits with hooks: Claude Code steps need the project trusted (run `claude`
  once in that folder and accept the trust dialog); Codex steps need the hooks
  trusted in Codex and `--codex-trusted`.

### 4.9 Autopilot (development flag `autopilot`)

Runs skill-kit flows unattended in Docker sandboxes on this Mac, using your
Claude subscription. The Mac must stay awake. Triggers can start a run (e.g.
GitHub issues with chosen labels, polled every 2 minutes), or use **Run once**.
Each run works on its own `autopilot/**` branch; on success DuckTerm pushes the
branch and opens a **draft** pull request. Nothing is merged. Pause from the
flow's card. Needs Docker and the `skill_kits` flow it runs.

### 4.10 Connectors (Code and Full)

The Connectors panel turns on MCP servers (GitHub, Railway, Porkbun) for
Claude Code and Codex. Tokens are not written into agent configs in plain
text; GitHub's token is resolved at launch from the `gh` CLI or the macOS
Keychain. Connectors can also run on one shared host
(`duckterm connector-serve`, `duckterm connector-attach --config FILE`).

### 4.11 Remote workspaces (Full, flag `byoc`)

- **Settings → Remote computers** manages saved SSH connections. Verify a new
  host in Terminal first; DuckTerm never accepts unknown or changed host keys.
  SSH credentials stay in your SSH config/agent.
- **New session → Run on → Remote** starts an agent on that computer; DuckTerm
  creates a separate checkout there. Local and remote sessions share one
  dashboard.
- Closing the app disconnects SSH but does not stop remote agents.
- Sign agents in on the remote computer itself: `claude auth login --claudeai`
  (verify `claude auth status`), `codex login --device-auth` (verify
  `codex login status`). For Codex device login, first enable **Device code
  authorization for Codex** in ChatGPT → Settings → Security.
- Local files and connectors are not copied to the remote computer.
- Cross-computer messaging between sessions is a separate flag
  (`cross_computer_collaboration`) set up in **Settings → Collaboration**.

### 4.12 Backups (flag `backup`)

`duckterm backup` requires the `backup` flag (section 4.13); without it, it
fails with "Backup is disabled by its feature flag".

```sh
duckterm backup                                  # to <home>/backups
duckterm backup --to /Volumes/Backup/duckterm/   # a directory
duckterm backup --to ~/Desktop/duckterm.tar.gz   # an explicit file
duckterm backup --to gs://BUCKET/prefix/         # Google Cloud Storage
duckterm backup --sync --to gs://BUCKET/mac/     # changed-file sync to GCS
```

- Archive contains: the database (consistent SQLite copy), `checkpoints/`,
  `snapshots/`, Claude transcripts (`~/.claude/projects/`) and Codex sessions
  and history (`~/.codex/…`), plus `manifest.json`.
- Excluded: session credentials, connector secrets, agent logins and configs,
  logs, worktrees, source code. Back up code with Git or Time Machine.
- Existing files are never overwritten. A failed upload keeps the local
  archive and prints its path.
- `gs://` destinations use your existing `gcloud` login, and need the `byoc`
  flag and the Full edition. DuckTerm does not create buckets.
- Dashboard: **Settings → Back up to remote** shows the saved destination and
  starts one backup; closing the dialog does not cancel it.
- No built-in schedule. Use launchd or cron with absolute paths.
- **Restore**: install the same or newer DuckTerm, stop DuckTerm and the
  agents, extract the archive into a new private directory, copy
  `duckterm/db.sqlite`, `checkpoints/` and `snapshots/` into a **fresh**
  DuckTerm home (never next to old `db.sqlite-wal`/`-shm` files), put
  `claude/projects/` back under `~/.claude/projects/` and Codex files under
  `~/.codex/`, start DuckTerm with that home, then resume sessions one by one.
  Running processes cannot be restored.

### 4.13 Feature flags

Settings → **Feature flags** shows each flag's release default and its state
on this installation (read-only). Restart DuckTerm after changing flag files.

Release switches, off by default in all editions, set in
`<home>/feature-flags.json`:

```json
{"flags": {"backup": true, "skill_kits": true}}
```

| Flag | Turns on |
|---|---|
| `backup` | Backup controls and `duckterm backup` |
| `byoc` | Remote/cloud workspaces and `gs://` backups (Full only) |
| `byok` | Using and managing your own API key (Settings → AI models) |
| `skill_kits` | Settings → Skill kits, Flows tab, `duckterm kit` |
| `harness_management` | Agent installation and setup (Settings → Agent setup) |
| `cross_computer_collaboration` | Settings → Collaboration across computers |

Rules: the file must contain only `{"flags": {...}}` with known names and
`true`/`false` values; anything else is treated as invalid and **all** release
switches turn off. Once `feature-flags.json` exists it is authoritative; an
omitted flag is off.

Development flags (unfinished work) go in `<home>/flags.json`:

```json
{"enabled": ["autopilot"]}
```

Names: `home`, `autopilot`, `background_rule_suggestions`, `support` (this
Support chat, Full only) (and `skill_kits`,
honored here only when `feature-flags.json` does not exist). Unknown names are
ignored. A malformed `flags.json` stops the server from starting.

A route for a disabled feature returns 403 (dashboard code
`feature_unavailable`). Flags are a local opt-in, not a security boundary.

### 4.14 AI models and API keys

DuckTerm's own AI features (Ask Oracle, checkpoints, PR review, rule
suggestions) use the backend chosen in **Settings → AI models**. Your coding
sessions always use their own harness login.

- **Harness login** (default): runs the call through your installed
  Claude Code (or Codex, in "Automatic" mode: Claude Code, then Codex). Free on
  your subscription; counts toward its usage limit.
- **Your API key** (flag `byok`): Anthropic, OpenAI, OpenRouter or a custom
  OpenAI-compatible URL. Billed per token by your provider. The key is stored
  in the macOS Keychain; **Forget key** removes it.
- Checkpoint summary model: Settings → Checkpointing.
- Advanced (server environment): `DUCKTERM_SUMMARIZER_CMD` (a shell command fed
  the prompt on stdin, e.g. `claude -p --bare`) overrides the backend;
  `DUCKTERM_SUMMARIZER=off` disables the settings-based backend.

### 4.15 Other features

- **AGENTS.md suggestions** (Code/Full): "Suggest from corrections" proposes
  rules from feedback you gave agents; you review before saving.
- **Ducky for iPhone**: in the Mac app menu choose **Pair iPhone…**, scan the
  code and approve with Mac authentication. Shows this Mac's sessions;
  **Allow conversations with agents** lets you read replies and send short
  messages. Messages queue until the agent's prompt is empty and nobody typed
  for 2 minutes; the queue expires after 15 minutes and is cleared by a
  backend restart. Permission prompts must be answered on the Mac. Networks
  with client isolation can block pairing. CLI: `duckterm ducky setup|pair|…`.
- **Notifications**: Settings → Desktop notifications (when an agent needs an
  answer).

## 5. `duckterm doctor`

Run `duckterm doctor` (app-only: the app's CLI path). It checks:

| Check | Result | Meaning and fix |
|---|---|---|
| jq | ✓ shared hook runtime / bundled with the app / installed | OK |
| jq | ✗ "jq not found — needed for the hook's blocking-approval path" | `brew install jq` (or apt/dnf). App installs bundle jq. |
| curl | ✗ "curl not found — the hook posts events" | Install curl with your package manager |
| server | ✓ "server reachable at URL" | OK |
| server | ✗ "no server reachable at URL" | Start it: open the app, or `duckterm serve` |
| server | ✗ "URL is answered by something that isn't Duckterm" | Another program holds the port. Identify it before stopping it. Desktop editions need their assigned port (section 2). |
| token | ! "auth token not created yet" | Start the server once; it writes `<home>/token` |
| token | ✗ "token file unreadable" | Fix permissions on the path shown |
| hooks (per agent) | ! "no global hooks installed" / "settings exist but no Duckterm hook" | `duckterm install-hooks --agent AGENT --global` |
| hooks | ✗ "hook points at a different/old script path" | Reinstall: `duckterm install-hooks --agent AGENT --global` (common after moving or reinstalling DuckTerm) |
| hooks | ✗ "codex: hook has async:true" | Codex skips async hooks. `duckterm install-hooks --agent codex --global` |
| hooks | ✓ "codex: hook installed (current script)" | If Codex sessions still don't appear: start `codex`, run `/hooks`, trust the DuckTerm hook |

Doctor checks hooks for `claude-code`, `codex` and `copilot`. A "no global
hooks installed" warning for an agent you don't use is harmless. Doctor cannot
see Codex's trust state. Exit code is 1 if any check fails.

## 6. Troubleshooting

### A session does not appear, or its state never changes

1. Run `duckterm doctor` and fix every ✗.
2. Hooks must be installed before the session starts. Restart (Stop → Resume)
   sessions launched before the hooks were installed.
3. Codex: trust the hook with `/hooks` inside Codex. Check `codex --version`
   is 0.155.x (0.159+ unsupported, section 2).
4. An agent started in a plain terminal shows only through hooks. To register
   it explicitly, start it with `duckterm run --name NAME AGENT`.
5. The `generic` harness never reports busy/waiting state.
6. Check you are looking at the right edition: each edition has its own data
   directory and sessions (section 2). Sessions in DuckTerm Lite do not appear
   in DuckTerm Code or Full.

### Dashboard won't load / port not responding

- CLI install: is `duckterm serve` still running in its terminal? Start it.
- "duckterm: Port N is already in use." followed by "Another Duckterm is
  already running": open the printed URL; the server is already up.
- "Port N is already in use" without that line: another program holds the
  port. Identify it before stopping it. For a separate setup, use a distinct
  `DUCKTERM_INSTANCE`.
- "port conflicts with the desktop profile": a desktop edition's CLI or
  environment must use that edition's port; drop `--port`/`DUCKTERM_PORT`.
- "server must bind a loopback host": `--host` must be 127.0.0.1, localhost or ::1.
- Old dashboard after an upgrade: restart the server, then reopen the window
  or reload the tab.
- Server not exiting on `duckterm restart` ("old server has not exited;
  replacement was not started", "cannot stop the server"): wait a few seconds
  and run `duckterm restart` again. If it still fails, report a bug with the
  exact message.

### Mac app "The bundled local server could not start"

- Another edition may be running; the app explains how to switch. Quit the
  other edition's app (or stop its `duckterm serve`) and reopen.
- Open the app from Applications, not from the DMG or Downloads.
- If bundled files are missing or damaged, download a fresh copy and replace
  the app.
- Use **Help → Report a bug… → Review diagnostics** to see the app's event log.

### "duckterm: command not found"

- App-only install: use
  `"/Applications/DuckTerm Lite.app/Contents/Resources/bin/duckterm"`.
- pipx: run `pipx ensurepath` and open a new terminal.

### "Open this edition's app before using workspace commands"

The CLI points at a desktop edition's data directory that has not been set up.
Open that edition's app once, then retry.

### "database at … is schema vN, but this DuckTerm supports up to vM"

The database was written by a newer DuckTerm. Older versions refuse to open
it to avoid damaging data. Fix: install the newer (or same) DuckTerm version
that wrote it. Or point `DUCKTERM_HOME` / `DUCKTERM_INSTANCE` at a data
directory that matches this version. Do not delete the database.

### "Cannot read feature flags at …" / "Invalid feature flags at …"

`flags.json` in the home is malformed. It must be `{"enabled": ["name", ...]}`.
Fix or remove the file, then start again.

### A feature is missing or returns "disabled by its feature flag" / "unavailable in Duckterm Lite"

- "… is disabled by its feature flag (X)": add `"X": true` to
  `feature-flags.json` (section 4.13) and restart.
- "… is still in development": add it to `flags.json` and restart.
- "… is unavailable in Duckterm Lite/Code": that feature needs a different
  edition (section 1).

### Ask Oracle: "no summarizer backend — install claude/codex/copilot or set DUCKTERM_SUMMARIZER_CMD"

No model backend answered. Fix one of:
- Install and sign in to Claude Code (`claude`) or Codex (`codex`); run it once
  directly to confirm it works.
- Settings → AI models: choose a harness login that is detected, or save an
  API key (needs `byok`).
- Set `DUCKTERM_SUMMARIZER_CMD` in the server's environment.

Checkpoints use the same backends; a failed checkpoint shows **Retry checkpoint**.

### Text was typed into an agent but not submitted

When DuckTerm types into an agent (Oracle reminders, relayed replies, control
tower messages, introductions) it pastes, waits, then presses Enter, and
retries Enter once. If the text is still sitting in the prompt, the result is
reported as stuck (`prompt-stuck`, or a 409 in the control tower). Fix: click
the session's terminal and press **Enter**. Leftover text in the prompt also
blocks later Oracle reminders, because they require an empty prompt.

### An idle agent is not reading its inbox

- Turn-end reminders reach Claude Code only, and only when a turn ends. An
  agent already idle will not see new mail by itself.
- Full edition: Oracle reminds idle agents (section 4.4). It skips sessions
  where someone typed in the last 2 minutes, where the prompt is not empty
  (clear any half-typed draft), that are waiting on you, or that have no new
  mail since the last reminder. Check `DUCKTERM_ORACLE` is not `off`.
- Lite/Code, Codex or Copilot: type into the agent "run `duckterm session
  inbox`", or use **Introduce session collaboration** in its Inbox tab if it
  has never been introduced.
- Existing Claude sessions may need hooks reinstalled
  (`duckterm install-hooks --agent claude-code --global`) and a restart before
  turn-end reminders work.
- The two sessions must be in the same workspace.

### `duckterm session …` fails

- "DUCKTERM_SESSION_KEY is missing; run this inside a Duckterm session": run it
  in a terminal DuckTerm launched, not your own shell.
- "internal helper agents cannot access session inboxes": DuckTerm's own
  helper processes have no inbox; expected.
- Target not found: run `duckterm session discover` and use the session ID.

### Skill kit flow refuses to run

- "this kit has hooks and Claude Code loads project hooks only in a workspace
  the owner trusted": run `claude` once in that project folder and accept the
  trust dialog.
- "this kit has hooks and Codex only runs project hooks the owner trusted":
  trust the hooks in Codex (`/hooks`), then pass `--codex-trusted`.
- "X is not on PATH": install that agent CLI.
- "run `duckterm kit install`": the project's kit was not installed on this
  machine; install it (the plan is shown first).

### Sessions show Interrupted after a reboot

Expected: a reboot ends agent processes. Right-click → **Resume** each one.

### Long pastes into a terminal

Pastes go to the agent in 512-byte chunks. If text appears truncated on an old
version, update DuckTerm.

### macOS blocks the app

Download a fresh copy from the official GitHub release. Official apps are
signed and notarized. If still blocked, report the exact macOS alert and the
release version.

### An agent is missing or cannot sign in

Run the agent CLI directly in a normal terminal and finish its own install and
login. DuckTerm uses the agent's existing login.

### Backup fails

- "Backup is disabled by its feature flag": enable `backup` (section 4.13).
- "A backup is already running": wait for the current job (Settings → Back up
  to remote shows it).
- GCS errors: check `gcloud` is installed and logged in, the bucket exists,
  and `byoc` is on in Full.
- A job shown as `interrupted` after a restart never resumes on its own;
  inspect the destination and start a new backup.

## 7. Limits and known behavior

- Codex 0.159+ unsupported (section 2).
- Codex permission prompts are answered in its terminal only.
- Codex, Copilot and generic sessions get no turn-end inbox reminder.
- Pending approval buttons are held in memory. After a server restart, answer
  the prompt in the agent's terminal.
- Agents run as your OS user; DuckTerm does not isolate them from one another.
- Losing the machine loses DuckTerm data and transcripts not covered by a
  backup.

## 8. Reporting a bug

- **Dashboard**: Settings → **Report a bug**. If a session is selected, its
  details are included.
- **Mac app**: Help → **Report a bug…** (can include a screenshot of the
  current window and app diagnostics).

The form shows what will be included and lets you remove any item: DuckTerm
version, database schema version, OS, and for a selected session its harness,
model, state and the last 20 event types with timestamps, plus hook and jq
status per harness. It excludes terminal output, transcripts, conversation
contents, credentials and absolute paths. You can attach up to 5 files (5 MB
each, 15 MB total).

DuckTerm does not send anything itself. It prepares an email draft and a ZIP
(saved under `<home>/bug-reports/`). Open the draft in your mail app, attach
files from the ZIP, and send it. If the email would be too long, download the
ZIP instead.

Include: release version, OS, install method (app or pipx), what you did, what
you expected, what happened, and the relevant `duckterm doctor` output. Remove
private project paths and account details first.
