# Agent memory backend

Implementation candidate on `feat/agent-memory`. This is not a release claim.
The owner-facing design remains “Agent memory — architecture, implementation plan and tests” in DuckTerm Artifacts.

## Continuity and storage

The DuckTerm card owns the conversation chain. Exact Claude and Codex identities come from current binding, prior restart metadata and checkpoint markers. There is no newest-conversation fallback. Checkpoints retain immutable native JSONL snapshots in the session's existing private checkpoint directory. Native cleanup can remove the original file without removing the saved snapshot. Deleting the session removes its snapshots and index. Removing a checkpoint reference revokes reads of that retained version; unreferenced bytes are currently reclaimed when the session is deleted. These new memory snapshots and indexes are local-only and excluded from existing backups. The existing native-provider transcript backup selection is unchanged.

A saved older prefix remains searchable if the live transcript disappears, but coverage is incomplete and automatic preparation refuses to treat that prefix as current history. Unknown native identities, ambiguous locators, malformed sources and permission changes are explicit failures. Non-text attachments are listed as unprocessed; text/tool coverage does not assert that every fact survives summarization.

The database schema is unchanged. Canonical summaries use existing immutable digest revisions. Checkpoints reference a revision and sources. The session card reads the short overview; ordinary progress can publish a newer overview while referencing the last whole-history baseline. It cannot certify that baseline's coverage for newer work. Timeline remains a read view.

## Retrieval

Commands use the calling session's existing credential:

```sh
duckterm memory sources
duckterm memory search "why did we choose GCP?"
duckterm memory read <source-handle> --record <record-id>
duckterm memory read <source-handle> --offset <next-offset>
```

Initial retrieval is restricted to the owning session. A handle contains a source ID and content version, never an arbitrary file path. Search results include a record ID and excerpt. Read pages return `next_offset`; offsets count Unicode characters. The SQLite FTS5 index is derived and rebuilt if corrupted. Reads recheck source versions, current grants and credentials before returning.

Native reads accept at most 512 MiB per raw file, 8 MiB per JSONL line and 64 MiB normalized text per source. Limits are reported as unavailable coverage, not silently truncated success. Native reads and indexing run outside the event loop. A small normalized source can be cached in RAM; the index is the durable cache.

## Automatic preparation

The selected target harness prepares a brief from available text and tool records in bounded chunks. Codex preparation does not require a Claude response. Provider calls are isolated from project instructions and DuckTerm session credentials; commands use a read-only/tool-disabled configuration and a bounded output/timeout. No fallback to another provider is automatic. A synthetic live Codex probe has passed; live Claude preparation remains unverified.

A later preparation reuses exact unchanged source prefixes. Rewrites, deleted sources, changed permissions and policy changes invalidate reuse. Each generated continuation is checked against its preceding context and new source chunk. This model validation is not a guarantee that no fact was omitted: originals remain retrievable. The full launch brief has a 32,000 UTF-8 byte budget; required context is never silently clipped to fit.

Preparation saves one revision and checkpoint through `ProgressCoordinator.persist`. Intermediate model outputs are temporary. Up to two jobs perform preparation concurrently; equivalent dialogs share a job with separate cancellation leases. A source/settings change refuses readiness. Provider failure leaves the current agent untouched.

## Version 1 HTTP contract

All preparation endpoints require the owner token and current host routing. Session credentials cannot access them.

- `POST /sessions/:key/restart-preparation`: `{request_key,binding:{session_key,source_generation,target:{harness,model}}}`; model is `{mode:"default"}` or `{mode:"explicit",id}`.
- `GET /sessions/:key/restart-preparation/:id`: preparation state, sequence, coverage and readiness proof.
- `GET .../:id?detail=full&cursor=N`: complete bounded brief plus paginated source references and gaps.
- `DELETE .../:id?request_key=KEY`: release that dialog's lease. This cannot cancel an already accepted switch.
- `POST /sessions/:key/restart`: existing harness/model/interrupt fields plus `request_key` and `memory:{version:1,preparation_id,snapshot_id,source_generation}`.
- `GET /sessions/:key/restart?request_key=KEY`: durable operation receipt for timeout/retry reconciliation.
- `DELETE /sessions/:key/restart?operation_id=ID`: cancel only that queued operation.

An explicit Switch revalidates the exact prepared target/model, CLI version, native generation, source versions, current work, process identity and draft. An old client cannot bypass preparation on the owner route. A queued switch whose source changes fails before stopping; preparing again requires another explicit Switch. There is no usage-limit-triggered switch.

Preparation jobs are ephemeral; server restart requires preparation again, while saved revisions/checkpoints remain. Restart receipts are durable (latest 100 per card). A repeated request with different contents conflicts. A failed target launch reports `source_stopped`, preserves old native recovery metadata and does not automatically restart the old provider. An ambiguous interrupted process transition reports `unknown`.

## Verification and remaining integration

Deterministic tests cover three conversation generations, retained versions, provider cleanup, malformed records, session/grant isolation, credential revocation during an asynchronous read, rewritten sources, incremental reuse, independent dialog leases, provider failures, stop-boundary changes, lost-response retries and launch recovery. Independent QA supplied six additional retrieval regressions; both diagnosed defects have been corrected in the candidate.

Still required before release: independent retest of the final commit, fresh committed full gate, reviewed UI integration, native transport verification, and an isolated live harness switch that retrieves an earlier fact. Product and release-dev have not been migrated. Public Mac signing/distribution remains deferred by the owner.
