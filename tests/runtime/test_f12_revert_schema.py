"""A DB stamped v5 by the reverted F12 release (#84) must still open and work."""

import json
import sqlite3
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore


def _stamp_f12_v5(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE session_work (id TEXT PRIMARY KEY, state TEXT)")
        conn.execute("CREATE TABLE session_work_events (id INTEGER PRIMARY KEY, work_id TEXT)")
        conn.execute("CREATE TABLE session_work_updates (id INTEGER PRIMARY KEY, recipient TEXT)")
        conn.executemany(
            "INSERT INTO session_work VALUES (?, ?)", [("work-1", "done"), ("work-2", None)]
        )
        conn.executemany(
            "INSERT INTO session_work_events VALUES (?, ?)", [(1, "work-1"), (2, "work-2")]
        )
        conn.executemany(
            "INSERT INTO session_work_updates VALUES (?, ?)", [(1, "kept — café"), (2, None)]
        )
        conn.execute("ALTER TABLE session_questions ADD COLUMN parent_request_id TEXT")
        conn.execute("ALTER TABLE session_questions ADD COLUMN work_title TEXT")
        conn.execute("PRAGMA user_version=5")
        conn.execute("ALTER TABLE sessions DROP COLUMN restart_json")


def _legacy_rows(conn):
    conn.row_factory = sqlite3.Row
    return {
        name: [dict(row) for row in conn.execute(f"SELECT * FROM {name}")]
        for name in ("session_work", "session_work_events", "session_work_updates")
    }


@pytest.mark.parametrize("version", [5, 8])
def test_retired_tables_are_archived_completely_before_drop(tmp_path, monkeypatch, version) -> None:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    path = tmp_path / "db.sqlite"
    HistoryStore(path).close()
    _stamp_f12_v5(path)
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version={version}")
        original = _legacy_rows(conn)
    store = HistoryStore(path)
    try:
        assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 11
        cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(sessions)")}
        assert "pinned" in cols
        assert "restart_json" in cols
        tables = {
            r[0] for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert not {"session_work", "session_work_events", "session_work_updates"} & tables
        archives = list(tmp_path.glob("retired-f12-*.json"))
        assert len(archives) == 1
        assert archives[0].stat().st_mode & 0o777 == 0o600
        archive = json.loads(archives[0].read_text())
        assert archive == {"format": "retired-f12-v1", "tables": original}
        assert "folder_tasks" in tables
    finally:
        store.close()

    # Reopening a migrated DB must not overwrite the original archive.
    before = archives[0].read_bytes()
    HistoryStore(path).close()
    assert list(tmp_path.glob("retired-f12-*.json")) == archives
    assert archives[0].read_bytes() == before


def test_failed_archive_aborts_startup_without_dropping_rows_or_advancing_version(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    path = tmp_path / "db.sqlite"
    HistoryStore(path).close()
    _stamp_f12_v5(path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=8")
        original = _legacy_rows(conn)

    def fail(*args):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr("duckterm.core.folder_tasks.private_write", fail)
        with pytest.raises(OSError, match="disk full"):
            HistoryStore(path)
    with sqlite3.connect(path) as conn:
        assert _legacy_rows(conn) == original
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    assert not list(tmp_path.glob("retired-f12-*.json"))
    # The same DB must successfully migrate once writing is available again.
    HistoryStore(path).close()
    archive = json.loads(next(tmp_path.glob("retired-f12-*.json")).read_text())
    assert archive["tables"] == original
