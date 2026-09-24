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
