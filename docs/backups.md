# Backups

```sh
duckterm backup
duckterm backup --to /Volumes/Backup/duckterm/
duckterm backup --to ~/Desktop/duckterm-backup.tar.gz
duckterm backup --to gs://YOUR_BUCKET/duckterm/
```

The default destination is `DUCKTERM_HOME/backups` (normally
`~/.duckterm/backups`). Each run creates a uniquely named `.tar.gz` with mode
0600. An explicit filename must end in `.tar.gz`; other paths are directories.
Existing files are never overwritten. Failed local creation leaves no published
archive. A failed cloud upload retains the complete local archive and reports
its path. GCS destinations are bucket/prefix names; Duckterm appends the unique
archive filename and uses the current user's `gcloud storage cp` authentication.
It neither creates a bucket nor changes cloud permissions.

Contents:

- `duckterm/db.sqlite`: SQLite online backup, including committed WAL contents,
  checked for integrity without migrating or pruning the source database.
- `duckterm/checkpoints/` and `duckterm/snapshots/`.
- `claude/projects/`: transcript JSONL and session index files.
- `codex/sessions/`, `codex/archived_sessions/`, `codex/history.jsonl`, and
  `codex/session_index.jsonl` when present.
- `manifest.json`: archive format, application version, and consistency notes.

`CLAUDE_CONFIG_DIR` and `CODEX_HOME` select alternate agent transcript roots.
The archive excludes session credential files, connector secrets, agent login
and configuration files, symlinks, logs, and source worktrees. Conversation text
and database records are preserved as written; this does not scrub secrets
someone pasted into a conversation. Code and uncommitted files need separate
repository or machine backups.

The database is a consistent SQLite snapshot. Agents write their own files
independently, so the archive is not a transaction spanning every process.
JSONL capture ends at the last complete line present when each file opens.
Pause agents before backing up if the database and all transcripts must reflect
one common quiet period. File read/truncation errors fail the backup rather
than silently publishing an incomplete archive.

## Manual dashboard API

Use **Settings → Back up to remote** in the header to review the remembered destination and
start a backup explicitly. The dialog shows progress, the archive/upload result,
and errors. Closing it does not cancel an active job; reopen it to see the result.
Opening the dialog alone never starts a backup.

The backend uses owner-authenticated routes; agent bearer credentials receive 403.

- `GET /backup` returns `{destination, job}`. Both start as null.
- `PUT /backup {destination}` remembers a local path or `gs://bucket/prefix`
  without running a backup.
- `POST /backup {destination?}` starts one background backup and returns 202
  with the current state. An optional destination is remembered first.
  No configured destination returns 400; an already-running backup returns 409
  with the current job and does not change its destination.
- Poll `GET /backup` while running. A job includes `id, status, destination,
  started_at, finished_at, archive_path, result, error`; timestamps are
  milliseconds. Status is `running, succeeded, failed,` or `interrupted`.
  `archive_path` is the local archive, including after an upload failure;
  `result` also identifies the GCS upload location on success.

Configuration and the latest result are stored privately in
`DUCKTERM_HOME/backup-state.json` with mode 0600. Changing configuration during
a run affects the next backup; each job retains its own destination.
A restart preserves completed results. A previously running job becomes
`interrupted`, with advice to inspect the destination before retrying.
It never resumes or schedules itself automatically.

The API calls the same archive implementation as the CLI in a worker thread.
It does not block dashboard requests, overwrite archives, create buckets,
change permissions, or upload any data until the owner starts a backup.
Missing `gcloud` or authorization errors are reported in the job result.

## Restore

1. Install the recorded DuckTerm version or a newer compatible version.
2. Stop DuckTerm and the agents whose state is being restored. Preserve the
   existing data directories before replacing anything.
3. Extract your own trusted archive into a new private directory. Inspect its
   `manifest.json` and contents before copying them into place.
4. Copy `duckterm/db.sqlite`, `checkpoints/`, and `snapshots/` into a **fresh**
   `DUCKTERM_HOME`. Never combine the restored DB with old `db.sqlite-wal` or
   `db.sqlite-shm` files. Put `claude/projects/` back under `~/.claude/projects/`
   and the archived Codex paths back under `~/.codex/` (or your configured roots).
5. Start DuckTerm with that `DUCKTERM_HOME`. Reauthenticate agent/provider
   accounts as needed. Session credentials are reissued by normal enrollment.
   Resume conversations individually; processes cannot be restored from a backup.

## Scheduling and offsite storage

Use launchd or cron to invoke the CLI with absolute paths and the desired
environment. There is no in-app scheduler. Retain Time Machine or another
machine backup; a local archive on the source disk does not survive disk loss.

Recommended offsite target: a private GCS bucket in the existing Google Cloud
project, using an already authorized user login. See the
[gcloud copy reference](https://docs.cloud.google.com/sdk/gcloud/reference/storage/cp).
No real transcript archive is uploaded by tests or installation.

For the remote workspace VM, use a GCP persistent-disk snapshot schedule through
the administrator's cloud account. This requires no cloud credentials inside
the VM. A reasonable starting policy is daily snapshots with 14-day retention;
review disk coverage and retention before creating it. Follow Google's
[snapshot schedule instructions](https://docs.cloud.google.com/compute/docs/disks/scheduled-snapshots).
The CLI implementation does not provision or attach this infrastructure policy.

## Optional changed-file sync to GCS

Full archives remain the default. To reduce repeat upload traffic, explicitly
select sync for a GCS destination:

```sh
duckterm backup --sync --to gs://YOUR_BUCKET/mac/
```

Each sync still creates and retains a complete private local `.tar.gz` archive.
It stages files from that archive, reusing the existing exclusions and complete
JSONL-line handling; it never points the transfer command at raw agent homes.
This saves network traffic, not local compression time or local archive space.

Under the chosen prefix, sync stores:

- `sync/current/`: filtered transcripts, checkpoints and snapshots, using the
  same relative layout as a full archive, without its database or manifest.
- `sync/db/duckterm-backup-TIMESTAMP-ID.sqlite`: a complete, consistent online
  SQLite snapshot with a unique name on every run.
- `sync/runs/duckterm-backup-TIMESTAMP-ID.json`: a completion receipt containing
  the database URI and current-tree URI, uploaded only after the other steps
  succeed.

The first run uploads all included files. Later runs use
[`gcloud storage rsync --recursive --checksums-only`](https://docs.cloud.google.com/sdk/gcloud/reference/storage/rsync)
to skip unchanged contents, even if staging timestamps differ. Changed files
are transferred whole; this is file-level sync, not binary block deduplication.
Remote-only objects are not deleted. The feature neither adds expiration rules
nor changes bucket settings. Use a separate destination prefix for each host,
and do not run concurrent syncs to the same prefix.

**This is a current-state recovery copy, not a historical snapshot of the file
set.** Updating an existing path replaces its current contents. Keeping an older
dated database does not preserve the matching older transcripts. Continue making
full archive uploads for self-contained historical restore points. Existing
remote archives are untouched.

A failed sync may leave the current tree partially updated. The error reports
this explicitly and gives the retained full local archive path. Retry sync, or
restore that archive. A previous completion receipt does not make the mutable
current tree atomic; a later failed run may already have changed some files.

### Restore a sync copy

Pause sync writers. Pick the database URI from a completed run's receipt and
copy its current tree into an **empty** directory. Do not restore over a live
installation. Replace the example URIs and directory below with the selected
receipt's values; use the latest successfully completed run for current-state
recovery.

```sh
umask 077
mkdir -p /tmp/duckterm-sync-restore/duckterm
gcloud storage rsync gs://YOUR_BUCKET/mac/sync/current/ /tmp/duckterm-sync-restore/ --recursive --checksums-only
gcloud storage cp gs://YOUR_BUCKET/mac/sync/db/SELECTED-DATABASE.sqlite /tmp/duckterm-sync-restore/duckterm/db.sqlite
sqlite3 /tmp/duckterm-sync-restore/duckterm/db.sqlite 'PRAGMA integrity_check;'
```

Require `ok`, then validate using the same installed server version:

```sh
DUCKTERM_HOME=/tmp/duckterm-sync-restore/duckterm python3 - <<'PY'
from pathlib import Path
from duckterm.persistence.history import HistoryStore
store = HistoryStore(Path('/tmp/duckterm-sync-restore/duckterm/db.sqlite'))
try:
    print([(row['session_key'], row.get('name')) for row in store.sessions()])
finally:
    store.close()
PY
```

Run that Python command in the DuckTerm environment. Follow the archive restore
instructions above to move verified files to their final locations and sign in
again. File snapshots are consistent per file, not one shared instant across a
running database and every transcript. Deleted local paths can remain in the
sync copy because retention is intentional.

### Sync API

`POST /backup` accepts optional `mode: "archive" | "sync"` alongside
`destination`. Omitting mode **always selects archive**, regardless of the last
job. Sync requires a `gs://` destination; invalid combinations return 400
without changing the remembered destination or starting a job. `PUT /backup`
continues to save only the destination. The owner credential requirement and
one-job-at-a-time behavior are unchanged.

Jobs include `mode`; older persisted jobs without it are archive jobs. Both
modes report `archive_path` for the retained local archive. Sync's `result`
includes the current-tree, database and completion-receipt URIs. UI mode controls
are a separate preview-reviewed integration; the existing button still creates
a full archive until that UI ships.
