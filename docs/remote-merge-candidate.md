# Remote-session merge candidate

The remote-session branch integrates main through `b458c63`. The source checkout
for main contains unrelated uncommitted changes; it has not been modified or merged.

## Behavior

- New Session keeps the destination, project source, agent, and prompt in one form.
  Existing folders, reviewed local copies, and repository clones are supported.
- Copies reconstruct worktrees as standalone Git repositories, preserving HEAD,
  staged and unstaged contents, selected untracked/ignored files, executable bits,
  and safe relative symlinks. Source files remain unchanged.
- Move to remote requires a stopped session and its exact recorded conversation
  UUID. Supported provider versions are Claude Code 2.1.267 and Codex 0.155.1.
  Unsupported versions and missing IDs fail explicitly.
- Snapshots are private, checksummed, and transferred in resumable chunks. An
  operation publishes only into a new directory. A durable launch claim prevents
  blind retries after lost responses. The local session remains recoverable.
- Broker admission now reserves capacity after the handshake without yielding.
  Forgetting a host removes its cached tunnel and refreshes the desktop picker.
- The merge preserves main's six connectors, shared-host enrollment, Google setup
  instructions, refresh behavior, typed rules, file editor, and backup CLI.

## Validation and practical limits

The post-fix local gate passed: 561 Python tests, 70 frontend tests, and 39 browser
tests. Four native unit tests and the real Swift transfer rehearsal also passed.
Python lint, formatting, types, and documentation checks passed.

Run `scripts/gate.sh`, `swift test --package-path mac`, and
`.venv/bin/python scripts/rehearse_project_transfer.py`. The rehearsal uses two
isolated local servers and the real Swift bridge; it uploads nothing remotely.
`mac/build.sh --test` produces the separate candidate app.

Transfer limits are 1 GiB including history and 50,000 files. Submodules, LFS,
credential-bearing repository URLs, unsafe links, missing required runtimes, and
explicitly unsupported Linux manifests are blocked. Dependency installation is
not automatic. Reviewed Git history and transcripts can contain sensitive content.
Private snapshots remain in the transfer directory for recovery; automatic
retention is not implemented. An ambiguous/exited launch requires inspection
instead of silently starting another process.

The prior VM persistence verification succeeded with both PIDs unchanged, but its
full duration and memory/log measurements were not recorded. That result does not
validate this new migration implementation.

## Isolated live candidate QA — 2026-09-22 UTC

The user approved candidate source/dashboard and synthetic fixture uploads. The
candidate runs on VM loopback port 4341 with a separate state directory and tmux
socket. Its systemd unit uses production's `KillMode=process`. An initial QA unit
omitted this setting and killed only its synthetic agent on restart; correcting
the unit made restart recovery pass. Production Claude PID 66209 was preserved.

Passed on the candidate:

- Real Swift Mac-to-Linux copy, repeated copy and launch, and launch retry after
  service restart. Source preserved and `.env` excluded.
- Git HEAD, index and working diff preservation; executable bits, relative links,
  and selected ignored files; existing destination rejection.
- Interrupted chunk upload across service restart, duplicate chunks and finish.
- Public `octocat/Hello-World` clone and retry.
- All synthetic provider pane PIDs survived a service restart; launch retries
  reused their existing sessions.
- Synthetic Claude transcript transferred by the candidate and resumed by exact
  path in Claude Code 2.1.267 print mode; it recalled the exact marker.
- Synthetic Codex transcript transferred and launched by the candidate in Codex
  0.155.1; it recalled the exact marker in the reviewed remote directory after
  accepting the normal synthetic-project trust prompt.

Live QA found and fixed a Codex resume bug: its recorded Mac cwd won over the
child process cwd. The launch now passes `--cd` with the reviewed destination.
The VM's Codex sessions directory was root-owned; ownership of that directory
alone was corrected to duckterm. Claude's interactive onboarding remains
incomplete; print-mode recall does not establish interactive acceptance. Local
Claude 2.1.232 remains outside the supported source version; the synthetic
transcript test does not claim an end-to-end Move from that installed version.
The isolated service has no connector credentials; provider MCP startup warnings
there do not validate or invalidate the separate live connector service.

## Remaining before merge

1. Finish native Test acceptance and provider setup, including the full stopped
   source-session Move flow on supported versions.
2. Private-repository clone authorization remains unvalidated. Automatic approval
   review rejected cloning the full project history as outside the earlier export
   approval; a separate user approval request is pending.
3. Arrange a safe VM reboot. A live Claude process was observed on the workspace;
   it has not been interrupted.
4. Complete user acceptance in RubberTerm Test, settle main's overlapping WIP,
   integrate any newer commits, and merge. No production release is included.
