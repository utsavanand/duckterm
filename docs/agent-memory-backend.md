# Agent memory backend

Implementation candidate on `feat/memory-complete`. This document describes the integrated code, not an installed-release or live-provider acceptance claim.

## Records and ownership

A DuckTerm session owns a chain of native conversations. Changing harnesses keeps the named session, tasks, inbox and artifacts; the new harness receives a new native conversation. Exact native IDs come from recorded bindings, restart metadata and checkpoint markers. There is no “pick the newest conversation” fallback.

Original conversation messages and event records are evidence. Session cards, tasks, inbox messages and artifacts retain their own canonical stores. Immutable summary revisions describe work over specific source ranges. A checkpoint is an explicit attempt and a reference to the published revision, source boundary and repository state; it is not another copy of the transcript. Timeline reads those records. Messages remains the conversation view.

The database schema is unchanged. Summary revisions and folds use existing digest storage. Typed relationships and explicit claim lifecycle changes use existing links. The retrieval index is a disposable SQLite FTS5 index, not a second source of truth. There is no vector database or separate property-graph service.

Checkpoints retain native JSONL snapshots in the session's private checkpoint directory. A retained prefix remains readable after native cleanup, but cannot certify newer unavailable history. These memory snapshots and indexes are local-only and excluded from the existing backup selection. Project-file backup and native transcript backup are separate features. Session deletion removes its snapshots and index; removing a checkpoint reference revokes access to that retained version. No automatic destructive retention or bulk artifact relabelling is enabled by this candidate.

## Manual checkpoints and independent summaries

History recording continues as work happens. Summary generation has no periodic or turn-end timer. Clicking Checkpoint explicitly requests an update. Change harness can prepare the required range through the same coordinator before offering the final switch.

The coordinator captures an upper source boundary and resolves already accepted ranges. Each new summary uses only a contiguous unsummarized span of original conversation/event input, never the previous summary. Successful tool output and routine metadata stay reference-only; owner messages, assistant conclusions, failures and relevant decisions remain eligible. Task, inbox and artifact bodies are retrieved separately rather than repeatedly inserted into summary prompts.

Manual checkpoints select the most recent 20 eligible conversation records by default. A record is a message or message segment, not a complete user/assistant turn. Instance settings can change the lookback to 1–1000 records. Larger windows are processed in batches of at most 20 records, with a 16,000-byte generation-prompt budget that may split a batch sooner. Already accepted records are reused; enlarging the window fills older missing ranges. Older history remains retrievable, and existing protected claims are retained. Completion means the selected window is covered, not that all history has been summarized. Coverage includes the selected count and the number outside the window.

Manual checkpoints default to the Claude `haiku` writer and `sonnet` reviewer aliases. Settings can choose `haiku`, `sonnet` or `opus` independently for each role. Existing custom summarizer command/URL overrides still take precedence. Preferences are captured when the checkpoint starts; changing Settings affects the next attempt. Settings are stored atomically in the instance's `checkpoint-settings.json`, served by owner-authenticated `GET` and `POST /checkpoint-settings`, without a database schema change.

Checkpoint generation reads a retained snapshot. New live messages and ordinary card updates belong to the next attempt; they do not invalidate the captured work. Conversation identity, sharing scope, summary policy, retained evidence and publication races remain checked. Capturing/materializing history still reads the source catalog before selecting the window, so the lookback bounds model input, not all initial filesystem work.

Harness-switch preparation retains its existing full-range behavior and 50-record batch ceiling; these manual-checkpoint preferences do not change it. Both paths keep the 16,000-byte generation-prompt budget, leaving 32,000 bytes of the overall 48,000-byte allowance for generation/review overhead. An indivisible record that cannot fit fails explicitly. The summary allows up to 2,000 characters and asks for about 100–200 words; structured claims have a separate budget. Local validation and a content review must both accept a draft. Rejected or truncated drafts never advance processed coverage.

A cold start can require many sequential bounded batches. Accepted immutable leaves survive a later batch failure and can be reused on retry when source hashes and boundaries still match. The current published revision advances only when the captured eligible range completes. Changes or deletions invalidate affected source coverage; a failed attempt leaves the previously published memory available. Legacy cumulative summaries remain readable but are not automatically converted into independent spans.

Canceling a preparation releases its lease. Work stops before the next provider batch when no other dialog or accepted manual checkpoint needs that range. A provider call has its own timeout. Full-range preparation rechecks live source, permission and native-file fences before publication and switching; a manual checkpoint checks its retained snapshot and current identity/scope as described above.

## Claims, folding and handoff budgets

Version 2 claims carry stable identity, original evidence and a kind: decision, constraint, learning, callout, question, unfinished work, goal or risk. Older untyped claims are shown as unclassified. Retirement or supersession requires an explicit authenticated transition with supporting evidence; a narrative summarizer cannot silently close a claim. Search exposes effective lifecycle state alongside saved state, and exact reads preserve the original text.

Before writing the 51st active summary at a tier, folding combines the oldest ten narratives into one higher-tier narrative. Claims and source references survive unchanged, and child revisions remain readable. The same rule applies at higher tiers. A failed optional fold does not prevent a valid new leaf from being saved.

A handoff assembles recent narratives, all open protected claims, current work, session expectations and the exact retrieval-tool instructions. Explicitly retired claims remain retrievable without consuming the active handoff budget. The active context is bounded at 10,000 bytes and the full launch brief at 32,000 UTF-8 bytes. If protected context cannot fit, preparation refuses before stopping the source agent. It does not silently trim claims, select a cheaper model or switch because a quota was reached.

The counters distinguish eligible records summarized from reference-only history. “92 of 92 eligible records” means those original records were supplied to accepted summaries; it does not mean every fact appears in the brief. Reference-only tool records are not missing conversation summaries. Older narratives omitted from the launch packet remain retrievable and their omission is declared.

## Retrieval and readable Memory

Commands use the calling session's existing credential:

```sh
duckterm memory sources
duckterm memory search "why did we choose GCP?"
duckterm memory read <source-handle> --record <record-id>
duckterm memory read <source-handle> --record <record-id> --offset <next-offset>
duckterm memory related <source-handle> --record <record-id>
duckterm memory link --file relationship.json
duckterm memory claim --file lifecycle-change.json
```

A source handle includes its version, never an arbitrary filesystem path. Search results identify an exact record; reads return a character-based pagination offset. Related reads follow evidenced links. Write tools validate the caller, relation, evidence, current versions and session scope. They do not replace original history.

Retrieval remains scoped to the calling session and currently permitted work records. Source versions, grants and credentials are rechecked before returning. Native reads stream the captured file prefix rather than imposing the former 512 MiB whole-file ceiling. Each JSONL line is limited to 16 MiB, and normalized text is limited to 64 MiB per source. Exceeding a bound reports unavailable coverage. Native reads/indexing run outside the event loop; a corrupt derived index can be rebuilt.

The owner opens Memory through **Read saved memory** in the right panel. Current memory assembles published spans, including earlier claims; selecting a revision reads that historical span separately. Expanding evidence performs an exact original-record read. Opening Memory never calls a summary provider. Missing or revoked evidence is withheld instead of returning cached text.

The session card and right panel show the last **successful** checkpoint age from saved outcomes. Failed or incomplete attempts do not reset it. No successful result reads “Never successfully checkpointed”; older records lacking enough outcome metadata read “Checkpoint age unknown.” The UI uses existing checkpoint reads and a display-only clock. It starts no generation timer.

Owner-only GET routes are `/sessions/:key/memory`, `/memory/read` and `/memory/related`; the latter two are under the same session prefix. An optional `revision` selects a saved revision. Mac routing admits only these exact read routes and validated query keys, preserving the selected host and native credential handling.

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

## Checkpoint request recovery

Checkpoint requests carry stable request IDs. Their bounded durable receipts survive a lost HTTP response; polling the exact receipt does not issue another checkpoint. A confirmed running receipt keeps the operation visible even when processing exceeds three minutes. Loss-of-contact timeout is separate from confirmed work. A crash after saving can reconcile from the persisted checkpoint. The UI distinguishes failure, incomplete coverage, running work and an unconfirmed connection result.

## Verification and limits

Candidate tests cover complete and failed multi-span ranges, retry reuse, source mutation, filtering, typed claims, folding, lifecycle replay, retired-claim budgets, current versus historical memory, lost responses, cancellation and native host isolation. Browser checks cover the approved Memory screen, original evidence expansion and the checkpoint clock. Model prompts are package resources and must be verified in both wheel and Mac bundles.

Deterministic tests do not prove that a generated handoff is useful. Real-history retrieval and a bounded real-provider handoff review remain separate acceptance steps. The earlier synthetic Claude-to-Codex probe established the launch/read-tool mechanism, not the quality or reliability of this complete candidate on a long production session.

The handoff-budget-refusal spec is only partly implemented: the shared final check and named budget exist, but an over-budget selected narrative/source map still refuses instead of shortening optional content with declared gaps. Protected claims must remain intact. Live acceptance on the product session remains outstanding.

Bulk artifact backfill, automatic legacy-summary conversion, automatic checkpoint cadence, automatic context trimming and an independent folder-retirement synthesis service are not implemented here. Folder tasks and other permitted records continue using their existing stores and access rules. Shipping or owner migration must not be inferred from an implementation commit or this document.
