# Folder details and artifact categories

The folder keeps Chat and Artifacts as its primary tabs. A collapsible right panel contains Activity, Artifacts by kind, and existing folder actions. It starts closed below 1250 px. Its widget order and visibility use the same versioned server layout store as Oracle, with folder rename/delete recovery.

Details are local to the selected folder and its descendants. Activity uses current session membership, UTC Today or seven calendar days, the existing transcript ledger, and durable mail accounting. Mail counts questions sent to or from a member once per question; broadcasts are separate and excluded. Unmatched transcripts and test sessions are excluded from usage. Refresh is explicit; artifact metadata changes also refresh the panel. There is no extra polling timer.

Artifacts support Decision (`kdd`), Spec, Research, Preview, Evidence, Report and Other. Existing files show an Inferred suggestion based on title/type. Agents may declare `--kind`; an owner's category overrides subsequent declarations. `PATCH /sessions/:key/artifacts/:id` accepts `kind` and/or boolean `kept` with owner authentication. Keep blocks saved-copy removal with HTTP 409 until explicitly undone.

Removal deletes the saved content while a companion metadata table retains title, source path, producer session, kind, original dates, size/hash and removal date. Removed entries are hidden until Show removed is selected, cannot be downloaded, and are counted in the kind breakdown. Deleting a session clears its artifact metadata as well as its files. Re-registering a removed path creates a new artifact rather than reviving its historical identity.

There is no automatic cleanup or default retention period in this change. The owner approved the UI previews; automatic retention remains a separate decision. Focus stays in its existing global header entry because it displays pinned sessions, not a folder filter.
