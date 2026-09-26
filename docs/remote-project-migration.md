# Remote project selection and session migration

Status: implemented in the remote-session candidate; live candidate QA is pending.
The Test build now keeps New session mounted when selecting a destination,
browses through a constrained native request/reply bridge, and switches the
dashboard only after successful remote launch. Tested against the live VM.
Project copy/clone and Move to remote now use a reviewed snapshot and a durable
operation journal. Local tests cover project integrity, retries, restart recovery,
and launch deduplication. The new candidate has not yet been validated on the VM.

Current supported limits: 1 GiB including Git history, 50,000 files, a new
unoccupied destination, Claude Code 2.1.267 or Codex 0.155.1 with an exact recorded
conversation UUID. Other provider versions and sessions without an exact ID fail
with an actionable error; no newest-transcript fallback is used. Submodules, LFS,
unsafe symlinks, embedded Git URL credentials, and incompatible Linux manifests
are blocked. Required project runtimes are checked, but dependency installation
remains an explicit user action.

Transfer progress and drafts survive reopening the form. Pause retains the
snapshot for retry. A lost launch response checks the recorded destination
session; an ambiguous or exited launch requires inspection and cannot blindly
spawn another process. Continue locally explicitly releases the source transfer
reservation and creates a separate continuation. Snapshots remain private under
Duckterm's transfers directory for recovery; automatic retention is not implemented.

## New session

Keep **Run on**, **Project**, agent, and prompt in one form. Changing Run on
connects in the background and updates destination-specific project choices;
it must not close/reopen the form or change the underlying dashboard. Preserve
portable fields and remember project choices separately for each computer.

When Remote is selected, offer three project choices:

- **Existing remote folder**: browse that computer and show its absolute path.
- **Copy local project**: choose a local folder and a new remote destination;
  review the transfer before copying. Local files remain in place.
- **Clone Git repository**: enter a repository URL and optional branch, then
  choose a new remote destination. Cloning does not include local edits.

Label both source and destination with the computer name. Show connection,
copy/clone progress, failures, and retry in place. Change the active dashboard
only after the remote session has been created successfully. Do not start an
agent against a partly transferred project.

Use a narrowly scoped native request/response bridge for saved-host connection,
project operations, and launch. Bind responses to the originating form and
request ID; discard stale responses after destination changes. Do not expose
an arbitrary URL proxy, raw server tokens, or arbitrary SSH commands to JS.

## Move an existing session

**Move to remote…** opens a review containing computer, source project, remote
folder, conversation support, transfer size, exclusions, and runtime checks.
Moving means copying project state and resuming the supported conversation;
a live process, shell memory, or an in-flight tool operation cannot be moved.

1. Require the source agent to reach a safe stopped/checkpoint state before
   taking the final snapshot. If it is working, explain that it must stop first.
   Do not silently terminate it or pretend a live directory scan is consistent.
2. Resolve the actual project/worktree and exact provider conversation ID.
   Never infer a conversation from the newest transcript in a directory.
3. Stage the project in a new private remote directory. Include tracked files,
   Git history needed for the working branch, staged/unstaged edits, and selected
   untracked files. Preserve executable bits and safe relative symlinks.
4. A Git worktree's `.git` file points outside its directory; copying that file
   alone is broken. Reconstruct a standalone repository, preserve HEAD/index
   and working-tree changes, and verify the result. Handle submodules and LFS
   explicitly; block unsupported cases with an actionable explanation.
5. Exclude OS caches, dependency directories, virtual environments, build output,
   sockets, and credentials by default. Show ignored files for deliberate
   selection: `.gitignore` is neither a complete secret filter nor a reason to
   discard every ignored file. Do not copy provider homes, `.ssh`, local auth
   state, or Git credentials. Flag embedded Git remote URL credentials.
6. Check available disk, destination collisions, required runtimes, and Linux
   compatibility. Show dependency/setup commands for approval; do not execute
   arbitrary repository install hooks merely to inspect the project.
7. Transfer only the selected conversation and validated Duckterm metadata.
   Use the destination's existing provider login and connector authorization.
   Explain that transcripts may contain sensitive task content. Old absolute
   paths in history remain historical; set the new working directory explicitly.
8. Verify the staged snapshot and publish it atomically within the same remote
   filesystem. Create a linked destination session exactly once, resume the
   verified conversation, and wait for a healthy attachment before declaring
   success. Retain the stopped local session and project for recovery.

Record an operation ID and explicit stages so cancellation, SSH loss, an app
restart, or a retry cannot overwrite a project or launch duplicate agents.
Resume transfer or clean up only the operation's own staging directory. After
success, show “Moved to <computer>” and a link from the local session. Continuing
locally later must be an explicit separate continuation, not an automatic retry.

Existing remote projects can be used for new sessions immediately. Migration
into an occupied folder is deferred: initially require a new destination to
avoid ambiguous merges and accidental overwrites.

## Evidence and acceptance tests

Completed narrowly scoped portability probes:

- Claude Code 2.1.267: copied one synthetic transcript, resumed with an explicit
  transcript path and `--fork-session`, and recovered the exact test phrase.
- Codex 0.155.1: copied one synthetic rollout into the remote sessions directory,
  resumed its exact UUID with an explicit remote working directory, and
  recovered the exact test phrase.
- Both used existing remote provider logins. No authentication files transferred.

These probes do not validate every provider version, tool-state restoration,
path remapping, or a complete project migration. Gate supported versions and
report unsupported conversation transfer rather than silently starting fresh.

Required QA before shipping:

- Form remains stable while switching targets; stale folder responses are
  ignored; cancellation preserves draft and does not switch dashboards.
- Existing folder, clone, ordinary folder copy, and Git worktree copy all start
  in the displayed remote path. Private cloning uses authorized remote access.
- Compare HEAD, index diff, unstaged diff, untracked contents, executable bits,
  and safe symlinks before/after transfer; test spaces and Unicode paths.
- Test submodules, LFS, ignored required files, secret exclusions, symlink escape,
  insufficient disk, existing destinations, unavailable runtimes, and Mac-only
  dependencies. Unsupported cases must stop before agent launch.
- Interrupt every transfer/handoff stage; retry and restart the app. Confirm
  source remains recoverable and destination launches at most once.
- For each supported provider, transfer a known conversation and changed project,
  resume exact conversation, and complete a task using both prior context and
  transferred edits. Verify remote connector identity and permissions.
- Reconnect after closing the Test app; verify both new and migrated sessions.
  Keep reboot/process-recovery QA separate from migration claims.
