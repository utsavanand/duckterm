# Agent memory backend

This document describes the code in this checkout. It is not an installed-release or live-provider acceptance claim.

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

Harness-switch preparation also offers a count or All, defaults to the configured window, and selects newest unprocessed records. Both paths keep the 16,000-byte generation-prompt budget, leaving 32,000 bytes of the overall 48,000-byte allowance for generation/review overhead. An indivisible record that cannot fit fails explicitly. The prose summary allows up to 6,000 characters; structured claims have a separate 10,000-byte budget. Local validation and a content review must both accept a draft. Rejected or truncated drafts never advance processed coverage.

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

Retrieval defaults to the calling session and currently permitted work records. Source versions, grants and credentials are rechecked before returning. Native reads stream the captured file prefix rather than imposing the former 512 MiB whole-file ceiling. Each JSONL line is limited to 16 MiB, and normalized text is limited to 64 MiB per source. Exceeding a bound reports unavailable coverage. Native reads/indexing run outside the event loop; a corrupt derived index can be rebuilt.

### Linked session memory

A parent may read its direct child's saved memory while both belong to the same
shared root. An owner-reviewed merge can also grant its destination read access
to the source. The receiving agent uses its own credential; it never borrows the
source's credential. These links allow reads, not edits, and do not grant access
to unrelated sessions or transitively to every session that a source can read.

```sh
duckterm memory sessions --limit 20
duckterm memory sources --session SOURCE_SESSION --kind revision --limit 20
duckterm memory sources --session SOURCE_SESSION --kind revision --cursor NEXT_CURSOR
duckterm memory read SOURCE_HANDLE --session SOURCE_SESSION --record summary
duckterm memory related SOURCE_HANDLE --session SOURCE_SESSION
duckterm memory search "why this decision" --session SOURCE_SESSION
```

Follow `next_cursor` to list more links or sources. A merge receipt identifies the
source session and the saved summary revision at review; the reviewed handoff
text remains in the receipt even if the owner edited it. Later source revisions
are separately discoverable through `sources`. Closing or archiving the source
does not delete its revisions or evidence. A retained merge link reports an
explicit unavailable result after permanent source deletion. Changing either
session's sharing root invalidates the saved merge grant. Authorization is
checked again after an awaited read, so a move during retrieval cannot return
content under a stale grant.

This implementation uses the existing parent pointer, fork-merge rows and
`digest_items` store. New merge links use the `session_memory_link_v1` bucket on
the **destination**, retaining a pointer after source deletion without copying
the source's memory. The general merge's inbox message, appended notes, receipt
and grant commit in one transaction. Fork merges retain their existing durable
intent and crash-recovery flow, now including a revision reference. Old general
merge receipts do not retroactively grant access to private history.

Fork previews expose `memory.summaryRevision`. New clients send this value as
`summaryRevision` with the reviewed merge request; if the source revision has
changed, the server requires another review. Legacy fork clients do not identify
the revision behind their edited text, so their receipt leaves that reference
empty rather than attributing it to a newer summary.

The general merge request accepts explicit `memory:true` and `closeSource:true`
choices; closing requires the memory link. Older clients omit both and retain
their existing behavior. Close-after-merge waits for a verified turn end and an
empty draft, then rechecks session identity and activity before stopping its
owned terminal. An already terminated terminal can finish closure without being
restarted. The existing lifecycle sweep retries pending closure after service
restart. Status reads report `sourceClosed` and `closeState:pending|closed`; the
send/retry response can explain a deferral such as `draft_not_empty` or
`memory_unavailable`. Inbox delivery and successful code integration remain
separate outcomes. A summary sent while a turn is running describes the reviewed
state; the source link is how the destination retrieves later saved work.

UI integration is a separate reviewed step: the merge result and Timeline should
open the source's existing Memory view. These backend fields do not by themselves
add a button to the installed application.

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

## Checkpoint selection and result reads

The configured count defaults to 20 meaningful, unprocessed conversation records.
A new manual-checkpoint plan uses `newest_unprocessed_first`, matching harness
preparation. Selected records are still summarized in conversation order. Older
unprocessed records remain available; selecting a recent window does not mark
the older backlog processed. Version 2 selection plans carry their order.
An already-open version 1 manual dialog retains its original oldest-first
meaning instead of silently changing the owner's confirmed selection.

At a batch boundary, the writer may receive up to two preceding original
messages from the same conversation. These supply context for a short response
such as an approval. They fit inside the existing input budget after the selected
records, are included in the input fingerprint, and may be cited. They are not
counted again as processed records. This is bounded adjacent context, not a
guarantee of complete conversational reconstruction. Earlier summaries remain
excluded from span generation.

The read-only `GET /sessions/:key/memory` endpoint distinguishes three scopes:

- No selector: the published session memory. `claims_limit` (default 25, maximum
  100) and `claims_cursor` page through combined typed claims. The
  `memory.pagination` response includes `snapshot`, `total`, `offset`, `returned`
  and `next_cursor`; `total_claims` and `returned_claims` are explicit aliases.
  Each saved revision retains its validation and size checks. Browsing the union
  does not apply a single generated revision's byte limit to the entire session.
  Generation and handoff budgets remain unchanged.
- `revision=ID`: one exact saved revision and its evidence. Selecting an older
  revision does not substitute the latest revision. `revisions_limit` (default 20,
  maximum 100) and `revisions_cursor` page the history list independently.
- `checkpoint=ID`: one attempt's result. The response contains
  `checkpoint.summary_update`, `attempt_coverage`, and paginated `batches` with
  exact revision IDs and their saved content. `revisions_limit` and
  `revisions_cursor` page those batches. A failed attempt remains failed even
  when some or all batches saved. Independent batch summaries are not presented
  as a separately verified combined summary.

New checkpoint receipts persist `attempt_coverage` separately from the
session-wide coverage union. Older failed receipts with `scope=refresh_attempt`
can supply the same evidence. An older successful receipt that only lists the
session-wide union returns `batch_coverage_known=false`; its membership is not
guessed. All these reads use existing records and add no schema or summarizer.

Cursors bind the authorized session, read scope and visible result. A changed
continuation returns HTTP 409 and requires reloading the first page. Missing
evidence withholds derived content. Current-memory browsing includes explicit
retired and superseded states; it does not infer new lifecycle transitions.
Automatic reconciliation of earlier claims remains a separate behavior decision.

## Verification and limits

Candidate tests cover complete and failed multi-span ranges, retry reuse, source mutation, filtering, typed claims, folding, lifecycle replay, retired-claim budgets, current versus historical memory, lost responses, cancellation and native host isolation. Browser checks cover the approved Memory screen, original evidence expansion and the checkpoint clock. Model prompts are package resources and must be verified in both wheel and Mac bundles.

Deterministic tests do not prove that a generated handoff is useful. Real-history retrieval and a bounded real-provider handoff review remain separate acceptance steps. The earlier synthetic Claude-to-Codex probe established the launch/read-tool mechanism, not the quality or reliability of this complete candidate on a long production session.

The handoff-budget-refusal spec is only partly implemented: the shared final check and named budget exist, but an over-budget selected narrative/source map still refuses instead of shortening optional content with declared gaps. Protected claims must remain intact. Live acceptance on the product session remains outstanding.

Bulk artifact backfill, automatic legacy-summary conversion, automatic checkpoint cadence, automatic context trimming and an independent folder-retirement synthesis service are not implemented here. Folder tasks and other permitted records continue using their existing stores and access rules. Shipping or owner migration must not be inferred from an implementation commit or this document.
