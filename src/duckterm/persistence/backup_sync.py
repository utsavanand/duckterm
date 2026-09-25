"""Upload changed, archive-filtered files plus independent SQLite snapshots."""

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from duckterm.persistence import backup


class SyncResult(NamedTuple):
    archive_path: str
    result: str


class SyncUploadError(backup.BackupUploadError):
    def __init__(self, archive_path: Path) -> None:
        super().__init__(archive_path)
        self.args = (
            "Sync failed; the remote current tree may be incomplete. "
            f"Complete local archive retained at {archive_path}. "
            "Retry sync or restore the archive.",
        )


def validate_mode(destination: object, mode: object) -> None:
    if mode not in ("archive", "sync"):
        raise ValueError("Backup mode must be archive or sync")
    if mode == "sync":
        if not isinstance(destination, str) or not destination.startswith("gs://"):
            raise ValueError("Sync mode requires a gs:// bucket prefix")
        backup.validate_destination(destination)


def _stage(archive_path: Path, stage: Path) -> tuple[Path, Path]:
    """Extract only regular entries from our freshly generated private archive.

    This reuses create()'s online DB backup, transcript allowlist, no-follow
    traversal, and JSONL boundary handling instead of syncing raw directories.
    """
    current = stage / "current"
    current.mkdir(mode=0o700)
    database = stage / "db.sqlite"
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if not member.isfile() or name.is_absolute() or ".." in name.parts:
                raise ValueError("Unexpected non-regular or unsafe backup archive entry")
            if member.name == "manifest.json":
                continue
            target = database if member.name == "duckterm/db.sqlite" else current / name
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Unreadable backup archive entry")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with source, os.fdopen(fd, "wb") as output:
                shutil.copyfileobj(source, output)
            os.utime(target, (member.mtime, member.mtime))
    if not database.is_file():
        raise ValueError("Backup archive has no SQLite snapshot")
    return current, database


def create(destination: str) -> SyncResult:
    """Retain a full local archive; sync only changed files, then publish a DB and receipt.

    A stable current tree is not a point-in-time historical backup. Never delete
    remote objects or change bucket lifecycle settings. Use a prefix per host.
    """
    validate_mode(destination, "sync")
    cli = shutil.which("gcloud")
    if not cli:
        raise RuntimeError("Install and sign in to gcloud before syncing a backup")
    archive_path = Path(backup.create())
    run_id = archive_path.name.removesuffix(".tar.gz")
    prefix = destination.rstrip("/") + "/sync"
    current_uri = prefix + "/current/"
    database_uri = prefix + f"/db/{run_id}.sqlite"
    receipt_uri = prefix + f"/runs/{run_id}.json"
    try:
        with tempfile.TemporaryDirectory(prefix=".duckterm-sync-", dir=archive_path.parent) as temp:
            stage = Path(temp)
            current, database = _stage(archive_path, stage)
            subprocess.run(
                [
                    cli,
                    "storage",
                    "rsync",
                    str(current),
                    current_uri,
                    "--recursive",
                    "--checksums-only",
                    "--quiet",
                ],
                check=True,
            )
            subprocess.run(
                [cli, "storage", "cp", "--no-clobber", str(database), database_uri, "--quiet"],
                check=True,
            )
            receipt = stage / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "format": 1,
                        "mode": "sync",
                        "database": database_uri,
                        "current_tree": current_uri,
                        "archive_name": archive_path.name,
                        "files_consistency": "per-file; current tree changes on later syncs",
                        "historical_restore": "Use a full archive for historical recovery",
                    },
                    indent=2,
                )
            )
            receipt.chmod(0o600)
            # Only a fully successful run gets a receipt. The current tree itself
            # is non-atomic and can already have changed if an earlier step failed.
            subprocess.run(
                [cli, "storage", "cp", "--no-clobber", str(receipt), receipt_uri, "--quiet"],
                check=True,
            )
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as exc:
        raise SyncUploadError(archive_path) from exc
    return SyncResult(
        str(archive_path),
        f"{archive_path}\nSynced to {current_uri}\nDatabase: {database_uri}"
        f"\nCompletion receipt: {receipt_uri}",
    )
