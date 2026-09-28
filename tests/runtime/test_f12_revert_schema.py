"""A DB stamped v5 by the reverted F12 release (#84) must still open and work."""

import sqlite3
from pathlib import Path

from duckterm.persistence.history import HistoryStore


def _stamp_f12_v5(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE session_work (id TEXT PRIMARY KEY, state TEXT)")
        conn.execute("CREATE TABLE session_work_events (id INTEGER PRIMARY KEY, work_id TEXT)")
        conn.execute("CREATE TABLE session_work_updates (id INTEGER PRIMARY KEY, recipient TEXT)")
        conn.execute("INSERT INTO session_work_updates (recipient) VALUES ('kept')")
        conn.execute("ALTER TABLE session_questions ADD COLUMN parent_request_id TEXT")
        conn.execute("ALTER TABLE session_questions ADD COLUMN work_title TEXT")
        conn.execute("PRAGMA user_version=5")


def test_f12_v5_database_opens_after_revert_and_keeps_its_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    path = tmp_path / "db.sqlite"
    HistoryStore(path).close()
    _stamp_f12_v5(path)
    store = HistoryStore(path)
    try:
        assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 6
        cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(sessions)")}
        assert "pinned" in cols
        kept = store._conn.execute("SELECT recipient FROM session_work_updates").fetchall()
        assert [r[0] for r in kept] == ["kept"]
    finally:
        store.close()
