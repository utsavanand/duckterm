# Conversation identity at launch

New Claude Code and Copilot conversations receive a UUID before their process
starts. The adapter declares `session_id_assignable`; Codex and generic adapters
continue to observe identity through their existing paths. The CLI must support
the adapter's `--session-id` flag. An unsupported command fails normally; DuckTerm
does not retry by silently creating a different conversation.

The launch event writes the session and its existing `restart_json.native_binding`
in the same transaction. A failed persistence sink prevents spawn. Explicit UUIDs
are preserved and validated; duplicate assignment to another card is rejected.
Resume, continue, conversation forks, remote-connect and explicitly disabled
persistence commands do not receive an automatically generated ID. Initial task
text is never parsed as command-line flags.

The schema stays at v11. Assigned identity uses the existing native binding;
new accepted observations are saved separately in `restart_json.native_observation`
so event retention cannot remove them. Existing generation bindings still take
precedence, including empty pending bindings. Historical sessions without a saved
binding or observation retain the legacy event-ID lookup. There is no startup
backfill, database migration, or transcript-recency scan. Already missing IDs are
not recovered by this change.

`HistoryStore.native_identity()` returns `native_id`, `source` and `status`.
Sources are `none`, `assigned`, `observed` and reserved `adopted`. Status is
`missing`, `pending`, `recorded` or `contested`. The existing `session_id_for()`
still returns only a usable ID or `None`. Generation-specific binding barriers
and contested state prevent fallback to an older observation or event. Matching hooks
verify an assignment; a conflicting current-generation start marks it contested
and blocks Resume. Stale-generation hooks cannot change it. Harness switches
clear the previous identity before launch and preserve its metadata for rollback.

`GET /sessions` adds `conversation_identity: {status, source, assignable}` for the
future reviewed UI. This is identity evidence, not transcript readiness. A saved
UUID with no transcript is not a resumable conversation. Hook configuration is
also separate from identity: installing hooks does not recover an orphan.

This patch adds no recovery picker, adoption endpoint, hook installation action
or automatic relaunch. Those require their separate owner-reviewed UI and exact
candidate validation. Existing unidentified sessions remain unidentified until
their original conversation can be established from evidence.
