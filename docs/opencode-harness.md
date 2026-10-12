# OpenCode harness implementation

The owner resumed implementation on October 11. The isolated branch is
`feature/opencode-harness` in `/tmp/duckterm-opencode-harness`. The implementation
includes main through `b7356fa`; the approved recovery UI is implemented.

**Candidate activated, not released.** OpenCode is registered in this branch.
New Session checks the selected computer for the pinned supported version before
offering launch. The owner approved the recovery badge and Messages notice on
October 11; both are implemented and verified with stored fixture data at desktop
and narrow widths in light/dark themes. Native HTTP launch now succeeds through
the real dashboard endpoint alongside a sibling session, with Messages visible.
Isolated Linux acceptance passed; final integration and release checks remain.

## Current validation

- QA found a Messages freshness race after awaited reads. The correction binds
  the last-check time to a matching content fingerprint, while preserving stable
  snapshot files when polling finds no changes. Fifteen focused history, race
  and process-ownership tests pass. Independent QA cleared the correction with
  26 focused checks, including actual-file content/mtime and generation coverage.
- Committed candidate `b2a667b` passed the full gate: Python/static checks,
  475 frontend tests and 123 browser tests. The pinned native Mac
  `--prepare --supervisor --http` acceptance also passed. Its committed archive
  imports and compiles. Log: `/tmp/duckterm-opencode-b2a667b-gate.log`.
- Owner-approved `b2a667b` remote QA passed on duckterm-dev with pinned Linux
  OpenCode: preparation, same-process recovery, prompt non-replay, model choices,
  sibling preservation and real HTTP launch/Messages. Existing agent processes
  were preserved; no fixture process remained. The remote bundle was removed.
- Release-dev reported three orphaned Mac fixture processes from earlier runs.
  Their exact fixture identity was verified, they were terminated, and `c490ec9`
  fixes native Stop by signalling the owned pane before removing its terminal.
  Fourteen focused checks and real Mac native supervisor/HTTP acceptance pass;
  the probe now asserts process exit. Independent QA cleared the boundary with
  24 checks. The full gate on `c490ec9` passed: Python/static checks, 475 frontend
  tests and 123 browser tests. Log: `/tmp/duckterm-opencode-stop-gate.log`.

- The pinned native `--prepare --supervisor` probe passes on the candidate:
  early terminal input is blocked; the first completed assistant message uses
  the requested model; the initial prompt includes the collaboration guide and
  literal owner task; Messages and parent Idle are verified. Recovery preserves
  the exact native PID/conversation without replaying the prompt, and a sibling
  terminal remains running.
- A deliberately lost prompt acknowledgment is reconciled after reopening the
  DuckTerm database. There is one native user message and no repeated write.
- Independent QA identified a false-Idle race after process exit during awaited
  observation work. Integrated correction `5d67618` checks ownership after each
  await. Both independently supplied regression cases pass.
- Fifteen focused readiness, process-exit and registry checks pass; seven
  collaboration introduction/guide-refresh checks pass. Actual subprocess tests
  cover version mismatch, failed exit, oversized output and cancellation/reaping.
- A clean archive of committed `e67d3e3` imports and compiles successfully.
- The full gate on frozen implementation `e67d3e3` passed: Python/static
  checks, 472 frontend tests and 122 browser tests. Log:
  `/tmp/duckterm-opencode-e67d3e3-gate.log`.

The probes use synthetic providers and fake keys, with no paid inference or
inherited provider credentials. All created DuckTerm cards are `test:true` and
are deleted. Native fixture conversations, owned tmux servers and isolated homes
are removed. Only the approved isolated QA bundle was uploaded. No production
installation or public release has occurred.

## Launch and recovery

The adapter pins OpenCode 1.18.34. Destination readiness and actual launch use
the same bounded asynchronous version check. Missing/incompatible installations
and unsupported custom TUI configuration produce actionable reasons; neither
check installs software or starts inference. Registry activation is separate
from binary installation.

A private per-card TUI configuration loads the bundled observer. Its authenticated
loopback reports include native version, process ID, canonical project directory,
launch generation, sequence and selected conversation. Selection HTTP success
alone is insufficient. The supervisor blocks input until fresh TUI evidence
proves the intended conversation is selected.

The durable journal uses existing restart metadata, without a schema migration.
It records native identity before selection and claims creation, selection and
initial prompt writes before sending them. A lost receipt never authorizes
another write: recovery reads the exact native message and verifies its identity,
content and requested model. Owner Stop, Archive and Merge fence late results.

Recovery rebinds the saved observer port and verifies the owned process, native
store identity, generation and conversation. A foreign port occupant, replaced
store or contested conversation refuses recovery without killing another process.
A native conversation switch makes the binding durably contested.

Native credentials stay in private launch files/environment, rather than tmux
command arguments. The existing mode-0600 one-use launch script exports literal
environment values and unlinks itself before executing the agent. User provider
configuration and credentials are not rewritten or copied.

## Messages and state

Messages come from bounded native API reads, never terminal-output scraping or
OpenCode's internal database schema. Pagination rejects repeated cursors and
foreign/duplicate identities. Cached snapshots are scoped to destination, store,
generation and exact conversation; a revision watermark rejects late older writes.
Unchanged content retains a stable checkpoint fence.

Lifecycle reconciliation checks native status, parent messages, questions and
permissions. A quiet terminal is not completion evidence. Missing or incomplete
native evidence becomes `unknown`; process ownership is rechecked after awaited
snapshot and cache work. Parent completion is required before reporting Idle.
Requested model and observed assistant model remain separate.

The proposed visible recovery treatment uses a “Status unavailable” badge and a
notice above the last verified Messages. Preview:
`/tmp/duckterm-opencode-status-preview.html`, artifact
`becf866299cc43d9b77c4cd6e1683a46`. The approved display is implemented.

## UI and capability boundaries

The owner approved the New Session/model/support layout (artifact
`1d8f02376ff9403c821ad52d6244b26b`). It includes OpenCode, optional literal
`provider/model` input and expandable support details. Model drafts follow the
selected computer. Each selection needs a fresh capability response, including
A→B→A transitions; stale or malformed replies cannot enable Launch.

Configured model suggestions remain unavailable; manual provider/model IDs work
in the native fixture. Provider authentication and configuration belong to the
selected computer. Native usage fields are preserved, but aggregate analytics
and exactly-once usage accounting are not supported yet.

Safe Restart, in-session model switching from DuckTerm, fork, programmatic
approval answers, automatic priority delivery and remote conversation transfer
remain unsupported. Questions and approvals use the OpenCode terminal. Owner
keystrokes work after preparation; automated terminal pasting remains disabled.
The initial collaboration guide is a separate, verified launch capability.
API-backed history does not yet provide maintained-memory source retention or
cross-harness switching.

The form was inspected in light/dark themes at desktop and narrow widths.
Implemented screenshots are artifacts `926b65bb3454448c87164791561c24ae` and
`f98dc52ed16446e79380d509b17b9b40`. The native read-only options bridge addresses
the selected destination without opening another DuckTerm window.

## Reproducing native acceptance

Run:

```sh
.venv/bin/python scripts/qa_opencode_identity.py /absolute/path/to/opencode --prepare --supervisor
```

The probe restarts itself with an allowlisted environment and creates isolated
HOME/XDG/config/cache/state/project paths. It uses a dedicated tmux server,
disables updates/default plugins/model fetching, and uses a deterministic
loopback provider with `fixture/alpha` and `fixture/beta`.

Pinned executable SHA-256:
`7b63b34fafabded7d9231f6a9032755d0cdeaf8b9d2b70df8e25535471469eea`.
Archive SHA-256:
`8522b70f545184b3a8d97c5ca4f814093b2476d72aebfda8c48bcd072ec31d1b`.
Source commit: `e9f8a210b9e2b1e13d375b84906069886eb3b767`.

The native probe uses the registered adapter. Add `--http` to exercise the real
dashboard launch endpoint and native Messages in a separate isolated child
server. No remote acceptance or production release is implied.

Upstream references: [pinned release](https://github.com/anomalyco/opencode/releases/tag/v1.18.34),
[TUI plugin contract](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/specs/tui-plugins.md).

Linux acceptance is prepared for the official 1.18.34 x64 baseline binary.
Archive SHA-256: `24b0d458d21ef548b2752166303defcf7f4945b049fb4876ab78dfaf86d81b27`.
Executable SHA-256: `9ca0b9953d49997601655e54f846a3efa464f237e47c6f1b04716d0f2e64c4c2`.
The existing duckterm-dev host is Linux x86_64 with Python 3.12 and tmux; OpenCode
is not on its PATH. The binary is downloaded and verified locally only. Remote
QA must use a disposable directory, synthetic providers and dedicated tmux
namespace; no production service restart or global installation is needed.
