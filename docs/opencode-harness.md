# OpenCode harness implementation

Status: backend foundations in progress. OpenCode is not registered in the
production picker and this checkpoint does not claim a working DuckTerm session.
The accepted design is the October 4 pre-code plan (artifact
`2dbdf0af4657401b965f497555872e44`), including its native identity proof gate.

## Pinned native evidence

The isolated macOS probe uses OpenCode 1.18.34. Archive SHA-256:
`8522b70f545184b3a8d97c5ca4f814093b2476d72aebfda8c48bcd072ec31d1b`.
Executable SHA-256:
`7b63b34fafabded7d9231f6a9032755d0cdeaf8b9d2b70df8e25535471469eea`.

Run `python3 scripts/qa_opencode_identity.py /absolute/path/to/opencode`.
It uses a temporary HOME/XDG/config/cache/project, no inherited provider secrets,
no provider calls, a dedicated tmux server and two synthetic native sessions.
Both native sessions are removed; the fixture server and tmux server stop.
The disposable home is removed after cleanup. No DuckTerm production card is
created or changed. Add `--models` to run the deterministic loopback provider:
two custom model IDs (`fixture/alpha`, `fixture/beta`) complete in the same native
conversation, with the selected model checked on each completed assistant
message. This exercises the shipped native client and history/catalog parsers,
including the prompt endpoint's empty HTTP 204 acknowledgment. It uses a fake
key and makes no paid provider calls; it does not prove DuckTerm model switching.

The pinned release exposes `api.route.current` through its TUI plugin API.
The shipped observer plugin reports the actual selected session ID, canonical
project directory, process ID, launch generation, sequence and readiness.
Selecting two different native sessions in the same directory was observed
successfully. A selection HTTP response is never treated as proof. On macOS,
`/var` versus `/private/var` can otherwise target a different native instance;
canonicalize the launch directory before creating/selecting sessions.

The observer is silent, bounded, loopback-only and carries no credentials in its
file. It does not send prompts, answer approvals or simulate terminal keys.
Selection validation rejects foreign/stale observations, expires after five
seconds without a heartbeat and stays contested after a conversation switch.

## Implemented boundaries

- `HookSpec` declares config-entry or file-drop transport. Existing JSON hook
  behavior stays unchanged. File drops require a marker and matching saved hash
  for update/removal; unowned files, edits and symlinks are refused. Directory
  descriptor operations prevent a replaced parent from redirecting writes.
- A stdlib native client pins loopback and version, ignores environment proxies,
  refuses redirects, bounds reads and never retries uncertain writes.
- Model normalization preserves custom `provider/model` IDs, including extra
  slashes in the model portion. Catalog presence is distinct from a configured
  provider; missing context windows remain unknown.
- Message normalization validates session/message/part ownership and stable IDs.
  Unknown usage stays unknown; usage fields remain separate, without invented
  aggregate totals or pricing. This is not yet the persisted Messages cache or
  exactly-once usage ledger.

## Remaining integration

Controlled launch and crash recovery must persist native identity before TUI
selection, block early input, withhold the prompt until fresh native proof,
reconcile uncertain submission by native message identity, and preserve the
same DuckTerm window/card. Next: ordered lifecycle reconciliation, cached native
history, selected versus observed model, honest capability presentation and
independent isolated-home QA. Programmatic approvals, safe restart, fork,
priority delivery and remote transfer remain unsupported until separately proven.
No capability is enabled solely by this foundation checkpoint.

Independent QA reproduced the pinned two-session identity probe and found
malformed role/part discriminators escaping as TypeError. The correction rejects
non-string discriminators as protocol errors while preserving unknown string
part types as unsupported. The foundation at `7e43b87` passed the full gate
(443 frontend and 119 browser tests plus Python/static checks). Subsequent
parser/204 corrections passed 52 focused tests and static checks, plus the
two-model native fixture. This does not replace end-to-end harness acceptance.

The New Session/model/support preview is artifact
`1d8f02376ff9403c821ad52d6244b26b`; visual implementation awaits owner review.
No production install, schema migration, provider credential change, remote
upload or public release is part of this checkpoint.

Upstream references: [pinned release](https://github.com/anomalyco/opencode/releases/tag/v1.18.34),
[TUI plugin contract](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/specs/tui-plugins.md),
[plugins](https://opencode.ai/docs/plugins/).
