"""Large restart context must not become recurring dashboard traffic or sort spill."""

import json
from contextlib import closing

from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def seed(history):
    for key, updated in (("old", 1), ("new", 3), ("tie", 3)):
        history.record(
            {
                "_id": key,
                "_ts": updated,
                "event_type": "SessionStart",
                "session_key": key,
                "runtime": "generic",
                "test": True,
            }
        )
    control = {
        "native_binding": {
            "runtime": "generic",
            "native_id": "native-new",
            "source": "assigned",
        },
        "saved_context": "large restart context " * 50000,
    }
    history.set_restart_control("new", control)
    return control


def test_session_sort_preserves_rows_metrics_and_ties_without_sqlite_payload_sort(tmp_path):
    with closing(HistoryStore(tmp_path / "db.sqlite")) as history:
        try:
            seed(history)
            history._bump_metric("new", "test")
            expected = [
                {**dict(row), "metrics": history.metrics(row["session_key"])}
                for row in history._conn.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC"
                ).fetchall()
            ]
            queries = []
            history._conn.set_trace_callback(queries.append)
            actual = history.sessions()
            history._conn.set_trace_callback(None)
            assert actual == expected
            assert [row["session_key"] for row in actual] == ["new", "tie", "old"]
            plans = [
                row[3]
                for query in queries
                for row in history._conn.execute("EXPLAIN QUERY PLAN " + query)
            ]
            assert not any("TEMP B-TREE" in plan for plan in plans)
        finally:
            history.purge_test_sessions()


def test_dashboard_omits_large_restart_context_but_keeps_identity_and_stored_context(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_ORACLE", "off")
    with closing(HistoryStore(tmp_path / "db.sqlite")) as history:
        try:
            control = seed(history)
            server = Server(history=history)
            seen = []

            async def stats(row):
                if row["session_key"] == "new":
                    # Internal enrichment still has the original restart fence.
                    assert json.loads(row["restart_json"]) == control
                    seen.append(row["session_key"])
                return {"context_tokens": 42}

            monkeypatch.setattr(server, "_transcript_stats_for", stats)
            for _ in range(2):
                status, data = dispatch(
                    server, "GET", "/sessions", {"x-duckterm-token": server.token}
                )
                assert status == 200
                assert len(json.dumps(data).encode()) < 20000
                rows = data["sessions"]
                assert all("restart_json" not in row for row in rows)
                assert [row["session_key"] for row in rows] == ["new", "tie", "old"]
                assert rows[0]["conversation_identity"]["status"] == "recorded"
                assert rows[0]["context_tokens"] == 42
                assert history.restart_control("new") == control
                assert json.loads(history.sessions()[0]["restart_json"]) == control
            assert seen == ["new", "new"]
        finally:
            history.purge_test_sessions()
