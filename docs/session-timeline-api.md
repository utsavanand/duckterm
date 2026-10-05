# Session timeline API

`GET /sessions/:key/timeline` is an owner-authenticated, read-only projection of stored session records. It does not generate summaries, run agents, read artifact contents, or start polling. Unknown sessions return 404; missing owner authentication returns 401; session bearer credentials cannot use this owner route.

Query parameters:

- `limit`: 1–100, default 50.
- `kinds`: comma-separated subset of `prompt,delivered,learned,next_action,completed,artifact,decision,checkpoint,restart,model,harness,needs-you`. Omit for all. Unknown kinds return 400.
- `before`: opaque `next_cursor` from the preceding page. Preserve it exactly and URL-encode it. It is bound to the session and normalized filters. Invalid cursors or repeated/unknown query parameters return 400.

Response:

```json
{
  "summary": {
    "text": "Existing stored progress summary",
    "harness": "claude-code",
    "model": null,
    "age_ms": 10000,
    "counts": {"prompt": 1},
    "total": 1
  },
  "entries": [{
    "id": "prompt:event-id",
    "ts": 1000,
    "kind": "prompt",
    "icon": "prompt",
    "one_line": "Investigate the error",
    "detail": {"text": "Investigate the error"},
    "refs": [{"source": "events", "id": "event-id"}]
  }],
  "next_cursor": null
}
```

`icon` is a semantic token equal to kind, not HTML or an asset URL. Render all text as untrusted text. `one_line` is whitespace-normalized and capped at 240 characters; detailed text is capped at 16,000 characters. Refs identify source records for deep links. Summary/counts describe the selected kinds across the pagination boundary, not only the current page. Summary text, age, harness and model are current values.

Ordering is descending `(ts, id)`. Cursor state retains the initial timestamp and source rowid upper bounds, excluding new inserts even if they carry older or identical timestamps. Each source returns at most `limit+1` candidates before merging. Refresh without a cursor to see new records. This is a live projection, not a retained database snapshot: edits, deletions, completion status changes and relay trimming can change existing entries between pages. No historical record is fabricated to compensate. Counts use SQL aggregation scoped to the selected session; sorting/counting costs can grow with its stored history. Existing source indexes are reused; some sources (notably checkpoints) lack session indexes, so no constant-time or universally index-bounded query claim is made. No schema change is introduced.

Sources and semantics:

- Prompts: persisted `UserPromptSubmit` events, using their event timestamps.
- Deliverables and learnings: digest `deliverables`, `learnings`, `user_learnings`, using first-seen `created_at`. A digest edit does not move its original entry.
- Next actions: first-seen entry plus a distinct completed entry at stored `updated_at` when status is done. This reflects the source's stored completion timestamp, not an invented timestamp.
- Artifacts: registration time, including removed snapshots retained in artifact metadata. `detail.removed` indicates a tombstone; no content bytes are returned.
- Decisions: answered session questions whose stored sender is owner, at `answered_at`; detail preserves question and answer without inferring an approval.
- Checkpoints: stored creation time, label and summary.
- Restart/model/harness: explicit `RestartCompleted`, `ModelChanged`, `HarnessSwitched` events only. If a producer does not record such events, no entries appear; current restart JSON is not treated as historical events.
- Needs-you: one entry per stored blocking/non-offer relay note at its creation time. Closed records include `duration_ms` from `closed_at`; unresolved duration is null. The relay retains at most 500 notes, so this is not a complete lifetime waiting-time ledger.

Absent source tables yield no entries. Ledger tasks, PRs and releases are not available sources and are not inferred. No UI is changed by this backend; UI/native routing integration and its reviewed layout are separate work.
