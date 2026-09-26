# Remote-session merge candidate

The remote-session candidate now integrates main through `dc9e6da`. The main
checkout has not been modified by this integration. The feature is validated for the requested merge into main.

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

The post-fix local gate passed: 676 Python tests, 102 frontend tests, and 57 browser
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

## Integration validation — 2026-09-25

Main through `d1c0512` is integrated and the full gate passes. Native unit,
clipboard, report-data, and actual WKWebView report UI checks pass. Both the local
Swift transfer rehearsal and the updated isolated Linux copy/restart-retry check
pass; the latter's test-flagged session was deleted. DuckTerm Test is built and
open, with its separate saved host pointing to QA port 4341. The owner has been
asked to check the integrated New Session form. Production Claude PID 66209 is
still running and has not been stopped. Main's prior uncommitted-work blocker is
resolved; the feature remains separate pending acceptance.

## Integration validation — 2026-09-26 UTC

Integrated main through `dc9e6da` (60 new commits since the previous integration).
The full gate passed: 676 Python, 102 frontend, and 56 browser tests. Four native
unit tests, the actual WebKit artifact-download probe, report UI checks, and the
real Swift transfer/retry rehearsal passed. The separate DuckTerm Test app builds.
The merge combines artifact downloads with remote-origin navigation restrictions
and resets navigation-load state when switching computers. No production release
or additional VM deployment was performed for this integration.

The remaining native Move check means the complete desktop flow from selecting
a stopped source session through review, transfer, and opening its resumed remote
conversation. Component tests and synthetic provider recall do not establish that
whole flow. Prior Claude onboarding/version limitations are still recorded above.

## Full native Move acceptance — 2026-09-26 UTC

Both supported providers passed the actual desktop flow: select a stopped source
session, review its project and exact transcript, confirm Move, then explicitly
open the remote session. The test used the production AppDelegate, WKWebView,
SSH bridge, and transfer backend. Only synthetic fixture data and `test:true`
launch instrumentation were added. The source servers and desktop bundle were
isolated; the destination was the approved VM QA service on port 4341.

Claude Code 2.1.267 was installed in a temporary Mac QA home; the user's normal
installation was unchanged. Codex used 0.155.1. Both interactive remote providers
recalled the exact synthetic marker. The source row stayed stopped, its original
transcript and files remained unchanged, and its durable link recorded the
remote session. The VM's existing Claude authentication was used; its incomplete
onboarding preference was temporarily initialized and restored after testing.
Normal trust prompts were accepted only for the synthetic projects.

Acceptance found two bugs and verified their fixes: transfer launch now persists
the session display name, and Open remote session carries its exact key through
the native bridge instead of relying on dashboard default selection. Regression
coverage checks name persistence and selection among multiple sessions.

The first final browser run lost a New menu while native UI probes were running;
the full gate was rerun without concurrent native windows. Synthetic destination
sessions, projects, and provider transcripts were removed. Production Claude PID
66209 was preserved. Reboot testing remains deferred to avoid interrupting it;
service restart and disconnect persistence are verified. Private-repository clone
QA remains optional and skipped; public clone/retry passed. Neither limitation
requires stopping the existing user process for this code merge.

No production release or application installation is included in this merge.
