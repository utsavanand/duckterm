import json
import os
import sqlite3
import subprocess
import tarfile
from pathlib import Path

import pytest

from duckterm.cli import main
from duckterm.persistence import backup_sync
from duckterm.persistence.history import HistoryStore


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "duckterm"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    history = HistoryStore(root / "db.sqlite")
    history.record(
        {
            "_id": "start",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "sync-probe",
            "test": True,
        }
    )
    history.set_meta("sync-probe", name="Backup sync probe")
    put(tmp_path / "claude/projects/project/session.jsonl", '{"text":"keep"}\n{"incomplete":')
    put(tmp_path / "codex/sessions/day/rollout.jsonl", '{"text":"old"}\n')
    put(tmp_path / "codex/archived_sessions/old.jsonl", "{}\n")
    put(tmp_path / "codex/history.jsonl", "{}\n")
    put(root / "checkpoints/probe/note.md", "checkpoint")
    put(root / "snapshots/probe/manifest.json", "{}")
    yield tmp_path, root
    history.purge_test_sessions()
    history.close()


class Cloud:
    def __init__(self):
        self.objects = {}
        self.transferred = []
        self.calls = []
        self.fail_step = None

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        assert "--delete-unmatched-destination-objects" not in argv
        assert "--no-ignore-symlinks" not in argv
        assert kwargs == {"check": True}
        if len(self.calls) == self.fail_step:
            raise subprocess.CalledProcessError(1, argv)
        if argv[2] == "rsync":
            assert "--checksums-only" in argv and "--recursive" in argv
            root, prefix = Path(argv[3]), argv[4]
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    assert not p.is_symlink()
                    assert p.stat().st_mode & 0o777 == 0o600
                    key = prefix + p.relative_to(root).as_posix()
                    data = p.read_bytes()
                    if self.objects.get(key) != data:
                        self.objects[key] = data
                        self.transferred.append(key)
        else:
            assert argv[2:4] == ["cp", "--no-clobber"]
            key = argv[5]
            assert key not in self.objects
            self.objects[key] = Path(argv[4]).read_bytes()
            self.transferred.append(key)


@pytest.fixture
def cloud(monkeypatch):
    cloud = Cloud()
    monkeypatch.setattr(backup_sync.shutil, "which", lambda _: "/test/gcloud")
    monkeypatch.setattr(backup_sync.subprocess, "run", cloud.run)
    return cloud


def test_second_sync_transfers_only_changed_files_and_whole_dated_database(source, cloud):
    home, _ = source
    prefix = "gs://test-bucket/mac/sync/"
    first = backup_sync.create("gs://test-bucket/mac")
    assert Path(first.archive_path).is_file()
    initial = set(cloud.objects)
    cloud.transferred.clear()
    backup_sync.create("gs://test-bucket/mac")
    assert len(cloud.transferred) == 2  # Only whole DB and completion receipt.
    assert not any("/current/" in key for key in cloud.transferred)
    changed = home / "codex/sessions/day/rollout.jsonl"
    stamp = changed.stat().st_mtime_ns
    changed.write_text('{"text":"new"}\n')  # Same size AND same mtime: content must win.
    os.utime(changed, ns=(stamp, stamp))
    (home / "codex/archived_sessions/old.jsonl").unlink()
    cloud.transferred.clear()
    backup_sync.create("gs://test-bucket/mac")
    assert [k for k in cloud.transferred if "/current/" in k] == [
        prefix + "current/codex/sessions/day/rollout.jsonl"
    ]
    assert prefix + "current/codex/archived_sessions/old.jsonl" in cloud.objects
    assert initial <= set(cloud.objects)  # No remote deletes or dated DB overwrites.
    databases = [v for k, v in cloud.objects.items() if "/db/" in k]
    assert len(databases) == 3
    for database in databases:
        assert database.startswith(b"SQLite format 3\x00")
        assert len(database) > 4096


def test_sync_excludes_secrets_symlinks_logs_and_restores_with_history_store(source, cloud):
    home, root = source
    for path in [
        root / "session-credentials/private.json",
        root / "secrets/gmail",
        root / "logs/server.log",
        root / "worktrees/code.py",
        home / "codex/auth.json",
        home / "codex/config.toml",
        home / "claude/settings.json",
        home / "claude/projects/project/auth.json",
    ]:
        put(path, "SECRET-MUST-NOT-UPLOAD")
    (home / "claude/projects/project/link.jsonl").symlink_to(home / "codex/auth.json")
    (root / "checkpoints/linked").symlink_to(home / "codex", target_is_directory=True)
    result = backup_sync.create("gs://test-bucket/mac")
    assert all(b"SECRET-MUST-NOT-UPLOAD" not in value for value in cloud.objects.values())
    assert not any(
        "link" in key or "credentials" in key or "worktrees" in key for key in cloud.objects
    )
    assert (
        cloud.objects["gs://test-bucket/mac/sync/current/claude/projects/project/session.jsonl"]
        == b'{"text":"keep"}\n'
    )
    database = next(value for key, value in cloud.objects.items() if "/db/" in key)
    restored_path = home / "restored.sqlite"
    restored_path.write_bytes(database)
    with sqlite3.connect(restored_path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    restored = HistoryStore(restored_path)
    try:
        assert restored.session("sync-probe")["name"] == "Backup sync probe"
    finally:
        restored.purge_test_sessions()
        restored.close()
    receipt = json.loads(next(value for key, value in cloud.objects.items() if "/runs/" in key))
    assert receipt["database"] in cloud.objects
    assert receipt["mode"] == "sync"
    with tarfile.open(result.archive_path) as archive:
        assert archive.extractfile("duckterm/db.sqlite").read() == database
    assert not list((root / "backups").glob(".duckterm-sync-*"))


@pytest.mark.parametrize("step", [1, 2, 3])
def test_partial_upload_failure_keeps_local_archive_and_publishes_no_receipt(source, cloud, step):
    _, root = source
    cloud.fail_step = step
    with pytest.raises(
        backup_sync.SyncUploadError, match="remote current tree may be incomplete"
    ) as error:
        backup_sync.create("gs://test-bucket/mac")
    with tarfile.open(error.value.archive_path) as archive:
        assert "duckterm/db.sqlite" in archive.getnames()
    assert not any("/runs/" in key for key in cloud.objects)
    assert not list((root / "backups").glob(".duckterm-sync-*"))


def test_validation_and_missing_gcloud_fail_before_creating_backup(source, monkeypatch, capsys):
    _, root = source
    monkeypatch.setattr(backup_sync.shutil, "which", lambda _: None)
    for dest in (None, "/local", "gs://", "gs://bucket/*"):
        with pytest.raises(ValueError):
            backup_sync.create(dest)
    assert main(["backup", "--sync", "--to", "gs://test-bucket/mac"]) == 1
    assert "Install and sign in to gcloud" in capsys.readouterr().err
    assert not (root / "backups").exists()
