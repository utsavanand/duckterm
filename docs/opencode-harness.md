# OpenCode harness implementation

Status: backend foundations and approved launch form in progress. The candidate
New Session form includes OpenCode, but its launch capability remains disabled.
It is not registered as a runtime and this checkpoint does not claim a working
DuckTerm OpenCode session.
The owner explicitly resumed OpenCode implementation on October 11 after the
temporary memory/checkpoint priority hold.
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

The owner approved the New Session/model/support preview (artifact
`1d8f02376ff9403c821ad52d6244b26b`) on October 10. The implemented form adds
OpenCode, optional provider/model entry with configured-model suggestions when
available, and expandable support details. Drafts are isolated by computer;
stale or malformed capability replies cannot enable Launch. The native bridge
reads the selected computer's fixed options endpoint without changing windows.
Model arguments are passed literally and do not carry over to another agent.

The current backend reports launch unavailable; adding the picker must not
silently use a generic runtime before controlled launch/recovery is integrated.
This also means model suggestions are currently empty. Backend delivery, not a
further layout decision, is the remaining requirement before enabling launch.
Independent UI QA found stale readiness after reselecting OpenCode; `e360802`
binds replies to the active selection generation. Ten focused component cases
cover that correction, including switching computers A→B→A during pending reads.
All 450 frontend tests pass on the correction, plus TypeScript and ESLint.
Five native bridge checks passed. Five focused browser scenarios pass on the
rebuilt correction, including existing custom-agent launch and remote selection.
The full gate at `72fdd44` passed Python/static and 448 frontend checks, but
finished with 119/120 browser passes because the older picker test expected four
agents. Its expectation now includes OpenCode and its focused rerun passes;
there is no claim of a subsequent full-gate pass on this checkpoint.

On October 11 the unchanged saved candidate `a7ab92e` passed the complete gate,
including its corrected browser expectations. Subsequent launch preparation
work adds a durable journal in existing restart metadata (no schema change),
generation-checked native creation/selection/prompt claims, and off-loop native
requests. It cannot repeat an uncertain creation or initial prompt. A prompt
receipt must match the exact native user message, content and requested model;
selection still requires fresh native TUI proof. Supervisor integration follows
in the next checkpoint; the New Session action remains disabled.

Run the pinned probe with `--prepare` to exercise that driver against the real
CLI and synthetic loopback provider. It drops the native prompt acknowledgment,
reopens the disposable DuckTerm database, and confirms the same conversation
and one initial prompt. The `test:true` card and all native fixture sessions are
deleted on cleanup. This is native preparation evidence, not full launch,
lifecycle, history or remote acceptance.

The next native acceptance step (`--supervisor`) now uses the real DuckTerm
session supervisor and terminal path. Every input path rejects early input;
the initial prompt is held until selection proof. The child runs with a private
TUI plugin configuration and authenticated loopback observer, and its exact
owned pane PID must match the observer. Recovery rebinds the original observer
port, verifies the retained PID and native store identity, and reconciles the
same journal. An occupied port, replaced store or contested binding refuses
recovery without killing a foreign process or interrupting other cards.

The pinned native supervisor fixture completed an assistant response using
`fixture/alpha`, reattached the same process/conversation after observer
shutdown, preserved a neighboring synthetic terminal, and verified that the
initial prompt was not replayed. All fixture cards carry `test:true` and are
deleted. This proves local native launch/recovery under the fixture. Live
lifecycle/Message-cache integration and final harness/remote acceptance remain;
the product registry and launch-capability response are still disabled.

API-backed Messages and lifecycle reconciliation now have a local native proof:
the supervisor fixture reads its completed assistant response through the
Messages cache, observes verified Idle, and retains identical message keys
after recovery. History pages are bounded and reject duplicate identities or
repeated cursors. Cache scope includes destination, store, generation and exact
native conversation; older workers cannot replace a newer snapshot. Unchanged
content stays stable for checkpoint fences. Native outages are unknown rather
than fabricated completion. Automated terminal pasting remains disabled for
OpenCode; owner keystrokes work after preparation. Usage fields remain native
measurements, not an enabled aggregate analytics capability.

The additional visible recovery treatment is awaiting owner preview review:
`/tmp/duckterm-opencode-status-preview.html` (artifact
`becf866299cc43d9b77c4cd6e1683a46`). No new UI recovery treatment is implemented
yet. Main-qa independently cleared committed recovery checkpoint `0a1f319`,
including cancellation while a native prompt worker is still completing. That
additional regression is now included with the preparation tests. Supervisor,
observer and history integration still require independent integrated review
and a fresh full gate before activation.

The integration gate completed successfully on October 11. Follow-up native
launch hardening moves sensitive environment values out of tmux command
arguments into its private one-use script, and the entire QA probe now starts
with an allowlisted environment. Both `--prepare --supervisor` pass together
under that clean environment. Owner Stop/Archive/Merge now fence late journal
results as well as new claims. These follow-ups and main integration need their
own final gate; no release-readiness claim follows from the earlier gate.

The form was inspected in light/dark themes and at desktop/narrow widths.
Dark form labels now use the existing theme text color. Screenshots are saved
as artifacts `926b65bb3454448c87164791561c24ae` (desktop) and
`f98dc52ed16446e79380d509b17b9b40` (narrow). A direct HTTP launch probe returned
400 with no new session row; the isolated visual QA server and state were removed.
No production install, schema migration, provider credential change, remote
upload or public release is part of this checkpoint.

Upstream references: [pinned release](https://github.com/anomalyco/opencode/releases/tag/v1.18.34),
[TUI plugin contract](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/specs/tui-plugins.md),
[plugins](https://opencode.ai/docs/plugins/).
