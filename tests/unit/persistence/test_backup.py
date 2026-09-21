import json
import sqlite3
import subprocess
import tarfile
from pathlib import Path

import pytest

from duckterm.cli import main
from duckterm.persistence import backup


@pytest.fixture()
def source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / ".duckterm"
    root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DUCKTERM_HOME", str(root))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    connection = sqlite3.connect(root / "db.sqlite")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA user_version=42")
    connection.execute("CREATE TABLE events (body TEXT)")
    connection.execute("INSERT INTO events VALUES ('committed WAL data')")
    connection.commit()
    yield home, root, connection
    connection.close()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def test_archive_restores_wal_database_and_transcripts_without_credentials(source, tmp_path):
    home, root, connection = source
    assert (root / "db.sqlite-wal").stat().st_size > 0
    put(root / "session-credentials/private.json", "session-secret")
    put(root / "secrets/gmail", "provider-secret")
    put(root / "checkpoints/session/checkpoint.md", "checkpoint")
    put(root / "snapshots/snap-1/manifest.json", "{}")
    put(home / ".codex/auth.json", "login-secret")
    put(home / ".codex/config.toml", "mcp-secret")
    put(home / ".codex/sessions/date/rollout.jsonl", '{"role":"user"}\n{"partial":')
    put(home / ".codex/archived_sessions/old.jsonl", "{}\n")
    put(home / ".claude/projects/project/session.jsonl", "{}\n")
    put(home / ".claude/projects/project/auth.json", "excluded-secret")
    put(home / ".codex/session_index.jsonl", "{}\n")
    target = Path(backup.create(str(tmp_path / "archive.tar.gz")))
    assert target.stat().st_mode & 0o777 == 0o600
    with tarfile.open(target) as archive:
        names = archive.getnames()
        assert "duckterm/checkpoints/session/checkpoint.md" in names
        assert "codex/archived_sessions/old.jsonl" in names
        assert "claude/projects/project/session.jsonl" in names
        assert "codex/session_index.jsonl" in names
        assert not any(
            "auth.json" in n or "credentials" in n or "secrets" in n or "config.toml" in n
            for n in names
        )
        assert (
            archive.extractfile("codex/sessions/date/rollout.jsonl").read() == b'{"role":"user"}\n'
        )
        database = tmp_path / "restore.sqlite"
        database.write_bytes(archive.extractfile("duckterm/db.sqlite").read())
        assert json.load(archive.extractfile("manifest.json"))["format"] == 1
    restored = sqlite3.connect(database)
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert restored.execute("SELECT body FROM events").fetchone() == ("committed WAL data",)
        assert restored.execute("PRAGMA user_version").fetchone() == (42,)
    finally:
        restored.close()
    assert connection.execute("PRAGMA user_version").fetchone() == (42,)


def test_skips_symlink_files_and_directories(source, tmp_path):
    home, root, _ = source
    folder = root / "checkpoints"
    folder.mkdir()
    secret = tmp_path / "outside-secret"
    secret.write_text("private")
    (folder / "secret.md").symlink_to(secret)
    (folder / "directory").symlink_to(home, target_is_directory=True)
    archive = Path(backup.create())
    with tarfile.open(archive) as stream:
        assert not any(n.startswith("duckterm/checkpoints/") for n in stream.getnames())


def test_refuses_overwrite_and_destination_recursion(source, tmp_path):
    _, root, _ = source
    target = tmp_path / "existing.tar.gz"
    target.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        backup.create(str(target))
    assert target.read_bytes() == b"preserve"
    with pytest.raises(ValueError, match="outside"):
        backup.create(str(root / "checkpoints"))


def test_gcs_uses_existing_cli_and_retains_local_archive_on_failure(source, monkeypatch):
    _, root, _ = source
    monkeypatch.setattr(backup.shutil, "which", lambda _: "/test/gcloud")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(backup.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="local backup retained"):
        backup.create("gs://test-bucket/private-prefix")
    archives = list((root / "backups").glob("*.tar.gz"))
    assert len(archives) == 1
    assert calls == [
        [
            "/test/gcloud",
            "storage",
            "cp",
            "--no-clobber",
            str(archives[0]),
            f"gs://test-bucket/private-prefix/{archives[0].name}",
        ]
    ]
    with tarfile.open(archives[0]) as archive:
        assert "duckterm/db.sqlite" in archive.getnames()


def test_missing_database_does_not_create_empty_backup(source, tmp_path, capsys):
    _, root, conn = source
    conn.close()
    (root / "db.sqlite").unlink()
    target = tmp_path / "missing.tar.gz"
    assert main(["backup", "--to", str(target)]) == 1
    assert "No Duckterm database" in capsys.readouterr().err
    assert not target.exists()
    assert not list(tmp_path.glob(".duckterm-backup-*"))


def test_cli_creates_a_unique_default_backup(source, capsys):
    assert main(["backup"]) == 0
    first = Path(capsys.readouterr().out.strip())
    assert first.is_file()
    assert main(["backup"]) == 0
    second = Path(capsys.readouterr().out.strip())
    assert second != first and second.is_file()
