"""Portable private archives: online SQLite backup plus durable transcript files."""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from duckterm import __version__
from duckterm.helpers import paths


def _database(source: Path, target: Path) -> None:
    if not source.is_file():
        raise RuntimeError(f"No Duckterm database at {source}")
    deadline = time.monotonic() + 60

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() > deadline:
            raise RuntimeError("Database backup timed out; retry when database activity settles")

    # Never instantiate HistoryStore: that would migrate/sweep the source DB.
    with (
        contextlib.closing(
            sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        ) as src,
        contextlib.closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst, pages=256, progress=progress)
        dst.execute("PRAGMA journal_mode=DELETE")
        if dst.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("Backup database failed its integrity check")


def _add_file(archive: tarfile.TarFile, fd: int, name: str) -> None:
    """Capture a fixed prefix of append-only transcripts, without partial JSONL records."""
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            return
        size = info.st_size
        if name.endswith(".jsonl"):
            # An agent can be halfway through appending a record. Preserve only
            # complete lines from the size observed when this file was opened.
            position = size
            while position:
                start = max(0, position - 65536)
                stream.seek(start)
                chunk = stream.read(position - start)
                newline = chunk.rfind(b"\n")
                if newline >= 0:
                    size = start + newline + 1
                    break
                position = start
            else:
                size = 0
            stream.seek(0)
        entry = tarfile.TarInfo(name)
        entry.size, entry.mtime, entry.mode = size, info.st_mtime, 0o600
        archive.addfile(entry, stream)


def _tree(archive: tarfile.TarFile, root: Path, prefix: str, transcripts: bool = False) -> None:
    if not root.exists():
        return
    if root.is_symlink():
        raise RuntimeError(f"Backup source must not be a symbolic link: {root}")

    def fail(error: OSError) -> None:
        raise error

    for directory, dirs, files, dirfd in os.fwalk(root, follow_symlinks=False, onerror=fail):
        dirs.sort()
        for name in sorted(files):
            if transcripts and not (name.endswith(".jsonl") or name == "sessions-index.json"):
                continue
            info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                continue
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
            relative = Path(directory).relative_to(root) / name
            _add_file(archive, fd, f"{prefix}/{relative.as_posix()}")


def create(destination: str | None = None) -> str:
    """Create locally first; retain the archive if an optional GCS upload fails."""
    root = paths.home()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"duckterm-backup-{stamp}-{uuid.uuid4().hex[:8]}.tar.gz"
    remote = destination if destination and destination.startswith("gs://") else None
    if remote and (not remote[5:].split("/", 1)[0] or any(c in remote for c in "*?#[\r\n")):
        raise ValueError("Use a GCS bucket or prefix without wildcards")
    if destination and "://" in destination and remote is None:
        raise ValueError("Backup destinations must be local paths or gs:// bucket prefixes")
    cli = shutil.which("gcloud") if remote else None
    if remote and not cli:
        raise RuntimeError("Install and sign in to gcloud before uploading a backup")
    output = Path(destination).expanduser() if destination and not remote else root / "backups"
    if output.is_dir() or not output.name.endswith(".tar.gz"):
        output /= filename
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite backup: {output}")
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))).expanduser()
    codex = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    sources = (
        root / "checkpoints",
        root / "snapshots",
        claude / "projects",
        codex / "sessions",
        codex / "archived_sessions",
    )
    if any(output.resolve().is_relative_to(source.resolve()) for source in sources):
        raise ValueError("Choose a backup destination outside the directories being archived")
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Staging next to the destination permits atomic publication and keeps the
    # database copy and incomplete archive private even in a shared destination.
    with tempfile.TemporaryDirectory(prefix=".duckterm-backup-", dir=output.parent) as temporary:
        stage = Path(temporary)
        database = stage / "db.sqlite"
        _database(root / "db.sqlite", database)
        database.chmod(0o600)
        archive_path = stage / filename
        fd = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as raw:
            with tarfile.open(fileobj=raw, mode="w:gz") as archive:
                archive.add(database, arcname="duckterm/db.sqlite", recursive=False)
                for directory in ("checkpoints", "snapshots"):
                    _tree(archive, root / directory, f"duckterm/{directory}")
                _tree(archive, claude / "projects", "claude/projects", True)
                for directory in ("sessions", "archived_sessions"):
                    _tree(archive, codex.expanduser() / directory, f"codex/{directory}", True)
                for name in ("history.jsonl", "session_index.jsonl"):
                    path = codex.expanduser() / name
                    if path.is_file() and not path.is_symlink():
                        _add_file(
                            archive, os.open(path, os.O_RDONLY | os.O_NOFOLLOW), f"codex/{name}"
                        )
                manifest = json.dumps(
                    {
                        "format": 1,
                        "duckterm_version": __version__,
                        "created_at": stamp,
                        "database": "duckterm/db.sqlite",
                        "database_consistency": "sqlite-online-backup",
                        "files_consistency": "per-file; pause agents for a common point in time",
                        "excluded": ["credential/config files", "worktrees", "logs", "symlinks"],
                    },
                    indent=2,
                ).encode()
                entry = tarfile.TarInfo("manifest.json")
                entry.size, entry.mode = len(manifest), 0o600
                archive.addfile(entry, io.BytesIO(manifest))
            raw.flush()
            os.fsync(raw.fileno())
        # link is atomic and refuses an existing name (including symlinks).
        os.link(archive_path, output)
    if remote:
        assert cli is not None
        uri = remote.rstrip("/") + "/" + output.name
        try:
            subprocess.run([cli, "storage", "cp", "--no-clobber", str(output), uri], check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                f"GCS upload failed; complete local backup retained at {output}"
            ) from exc
        return f"{output}\nUploaded to {uri}"
    return str(output)
