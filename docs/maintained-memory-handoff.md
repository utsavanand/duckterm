# Maintained memory and handoff integration contract

Implementation candidate, 2026-10-07. This backend change is not a shipped feature.
The owner requested parallel work in existing Codex sessions: main-dev owns this
backend and retrieval/context links; remote-session-dev declined that assignment
because of a separate direct owner priority. UI-dev and main-qa have pending
assignments for UI transport/preview and independent verification respectively. Startup is the sole
existing-session acceptance target; Product and release-dev are unchanged.

## One summary writer

`ProgressCoordinator.refresh` remains the canonical writer. For a supported native
conversation it captures original conversation, session, task, inbox, event and
text artifact records, excluding progress/revision/timeline projections and the
injected continuation packet. Each automatic update supplies at most 48,000 bytes
of new complete records, current required work and supported prior context to the
configured summarizer, then validates the resulting digest and richer context.

The existing short session-card digest and its append-only items remain compatible.
The immutable `summary_revision_v1` record additionally carries:

- `continuity.context`: overview and goals/constraints/decisions/unfinished/questions/risks.
  Claims are `{text, refs}`; refs use `source-id:version:record-id`.
- `continuity.processed`: source ID to record ID to hash of `[id, role, text]`.
  This measures records supplied to successful summary updates, not complete semantic recall.
- `continuity.frontiers`: the number of original positions observed per source. Updates label
  native records as initial history, historical backfill or new since the previous update;
  processing an older record later must not make it a newer owner correction.
- `continuity.verified`, available/summarized/remaining record counts, gaps and invalidation.
- `retained_sources`: exact non-native text references, described below.
- `memory_sources`: existing exact native conversation snapshots.

A changed/deleted processed record, policy change or sharing-scope change currently
invalidates reuse of the prior context conservatively. Appends reuse unchanged
record coverage. Required work is never silently treated as fully processed if it
exceeds the bound. Records larger than one update remain explicitly uncovered and
readable; they do not cause repeated empty model updates. Finer chunking and
claim-level invalidation are future optimizations, not completeness claims.

Automatic attempts happen at eligible turn ends with new events, no more often
than every 15 minutes. The attempt mark is in existing restart JSON so failed
attempts remain throttled after a server restart. Exit uses that same cadence.
There is no idle wake-up timer. Manual Checkpoint bypasses the interval, reuses
unchanged verified input, or joins an equivalent update in flight. A different
maintained-memory request waits and then captures fresh input. Failure preserves
the previous revision; coverage never advances on a failed validation.

## Checkpoint and retained versions

Checkpoints remain immutable markers in the existing table, with `summary_ref`
pointing to a revision or null, their own source boundary, and retained originals.
The marker does not contain another generated summary. A saved checkpoint can
therefore have an unavailable, stale or partial summary. Timeline remains a read
view and does not become an input to the summarizer.

`memory_versions.retain(key, materialized_source, root)` returns an immutable
reference `{id, version, kind, title, artifact_id?, root, text_snapshot}`.
`memory_versions.read(key, canonical_reference, current_root)` validates exact
bytes, content hash, source identity and sharing root, and refuses symlinks.
Call both off the event loop. Generic text snapshots live beside existing native
snapshots in the session's private checkpoint memory directory. Repeated identical
content resolves to the same path. Native snapshots keep their existing format.

The retrieval integration MUST enumerate canonical checkpoint/revision/link
references, not orphan files. Old text remains readable only while a canonical
reference exists and the source remains present and allowed by current grants.
Deletion/revoked sharing must not be bypassed through an old reference. Native
transcript cleanup may still use its authorized retained snapshot. The new helper
alone is not the authorization layer and does not yet make old artifacts readable
through the public memory API; main-dev is implementing that integration.

No schema migration, new dependency or cloud upload is included. Existing backup
selection excludes retained memory snapshots; cloud backup expansion stays deferred.

## Preparation and UI wire shape

Opening Restart makes a deterministic packet: maintained context, intact required
current work, selected complete recent conversation records, source references and
onboarding instructions with installed commands. It does not call a summarization
provider and does not create a new generated summary revision. The packet is at
most 32,000 UTF-8 bytes. Required context overflow fails before stopping anything.
The onboarding envelope names the stable session, project directory, source harness
and requested target/model selection; a default model remains explicitly unresolved.
Only currently implemented commands are listed; add graph commands after integration.

Keep the version-1 request/binding/proof identity and routes. Add:

```json
{
  "coverage": {
    "available_text": "not_processed",
    "retrieval": "available",
    "retention": "retained_snapshot",
    "handoff": {
      "method": "maintained",
      "summary_revision_id": null,
      "summary_generated_at": null,
      "summary_state": "unavailable",
      "available_records": 100,
      "summarized_records": 0,
      "included_records": 8,
      "omitted_records": 92
    }
  }
}
```

`available_text` is `not_processed` when summarized_records is zero; it is `partial` for some covered records and `processed` for all available
original records. UI must accept a ready maintained packet with honest partial or
unavailable summary coverage only when retrieval, retention and blocking-gap checks
pass. It must not display all history processed simply because preparation is ready.
`proof.revision_id` is now nullable. Other proof fields remain unchanged. Included
records count selected recent originals; current structured work and the summary
are separately present in the packet. Status omits the private brief; details
returns it and paginated exact read handles.

Final source, sharing, generation, model, policy, draft, request-key and stop-boundary
checks remain. A new generated summary is not proof that a process may be stopped.
A saved checkpoint is not proof that a harness switch succeeded. The owner still
explicitly confirms the switch. No Product/release-dev migration is automatic.

## Remaining integration and acceptance

1. Retrieval/catalog/search/read support for immutable revisions and retained versions.
2. Typed, evidenced related/link commands and stable claim identities; preserve own-session scope.
3. UI transport plus owner-reviewed freshness/coverage preview and visible integration.
4. Complete integration and independent QA before any further live preparation/switch,
   per the owner's latest direction. Then run the committed-tree full gate, package
   installation and startup-only acceptance.
5. Actual continuing agent retrieves an old fact absent from its brief and the exact
   cited artifact version. Measure real preparation time; no production timing claim yet.

Current main worktree: `/tmp/duckterm-maintained-memory`, branch
`feat/maintained-memory-handoff`. No live session was prepared, switched or stopped
while developing this candidate. Signed Mac distribution remains deferred.
