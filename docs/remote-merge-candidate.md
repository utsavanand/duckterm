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

Final local gate passed: 560 Python tests, 70 frontend tests, and 39 browser
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

## Remaining before merge

1. Approve source and built-dashboard upload to `duckterm-dev` for an isolated QA
   service. Automatic approval review rejected this export because general remote
   QA authorization did not explicitly cover that payload.
2. Validate the candidate on Linux: both supported provider migrations, actual
   clone authorization, interrupted transfer/handoff, reconnect, and native UI.
3. Arrange a safe VM reboot. A live Claude process was observed on the workspace;
   it has not been interrupted.
4. Complete user acceptance in RubberTerm Test, settle main's overlapping WIP,
   integrate any newer commits, and merge. No production release is included.
