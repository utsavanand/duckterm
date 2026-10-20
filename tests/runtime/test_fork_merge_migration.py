"""Fork merges must survive both the pre-release v9 and Tasks-first v10 paths."""

import sqlite3
import time

import pytest
from tests.runtime.test_folder_tasks import task
from tests.runtime.test_session_api import enroll

from duckterm.persistence.history import HistoryStore


def seed(history):
    for key in ("parent", "child"):
        history.record(
            {
                "_id": key,
                "_ts": int(time.time() * 1000),
                "session_key": key,
                "event_type": "SessionStart",
                "test": True,
                "runtime": "claude-code",
                "parent_session_key": "parent" if key == "child" else None,
            }
        )
        history.set_meta(key, name=key, group="work")
        enroll(history, key, "work")
    history._conn.execute("UPDATE sessions SET launched=1")
    history._conn.commit()


def merge(history):
    record = history.fork_merges.prepare(
        "child",
        {"summary": "Preserve merged child 🦆", "keepOpen": False, "requestKey": "migration"},
        lambda key: True,
    )
    history.fork_merges.finish(record)
    history.set_state("child", "merged")
    history.session_api.mark_delivered([record["message_id"]])
    history.fork_merges.delivered(record["id"])
    return record


def assert_final(history):
    assert history.session("child")["state"] == "merged"
    history.set_state("child", "busy")
    assert history.session("child")["state"] == "merged"
    history.record(
        {
            "_id": "late-start",
            "_ts": int(time.time() * 1000),
            "session_key": "child",
            "event_type": "SessionStart",
            "test": True,
        }
    )
    assert history.session("child")["state"] == "merged"


def test_v10_without_merge_schema_preserves_tasks_and_adds_final_merges(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    path = tmp_path / "tasks-first.sqlite"
    history = HistoryStore(path)
    seed(history)
    credentials = enroll(history, "parent", "work")
    saved = task(history, credentials, body={"title": "Existing task 🦆"})["task"]
    history.close()
    # The shipped Tasks-only schema had neither of these fork-merge additions.
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE fork_merges")
        conn.execute("ALTER TABLE sessions DROP COLUMN merged_into")
        conn.execute("PRAGMA user_version=10")
    for first_open in (True, False):
        history = HistoryStore(path)
        try:
            assert history._conn.execute("PRAGMA user_version").fetchone()[0] == 12
            assert history.folder_tasks.get(saved["id"])["title"] == saved["title"]
            if first_open:
                merge(history)
            assert_final(history)
            assert history.session("child")["merged_into"] == "parent"
            assert len(history.checkpoints("parent")) == 1
            assert len(history.fork_merges.list("parent", lambda key: True)) == 1
        finally:
            history.close()


@pytest.mark.parametrize("delivery_recorded", [True, False])
def test_v9_merged_child_and_delivery_checkpoint_survive(tmp_path, monkeypatch, delivery_recorded):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    path = tmp_path / "merge-first.sqlite"
    history = HistoryStore(path)
    seed(history)
    record = merge(history)
    expected = [tuple(row) for row in history._conn.execute("SELECT * FROM checkpoints")]
    if not delivery_recorded:
        # Crash after inbox delivery but before the callback records a checkpoint.
        history._conn.execute("DELETE FROM checkpoints")
    history._conn.execute("PRAGMA user_version=9")
    history._conn.commit()
    merges = [tuple(row) for row in history._conn.execute("SELECT * FROM fork_merges")]
    history.close()
    for _ in range(2):
        history = HistoryStore(path)
        try:
            assert history._conn.execute("PRAGMA user_version").fetchone()[0] == 12
            assert [
                tuple(row) for row in history._conn.execute("SELECT * FROM fork_merges")
            ] == merges
            checkpoints = history.checkpoints("parent")
            assert len(checkpoints) == 1
            assert checkpoints[0]["summary"] == record["summary"]
            if delivery_recorded:
                assert [
                    tuple(row) for row in history._conn.execute("SELECT * FROM checkpoints")
                ] == expected
            assert_final(history)
            assert history.folder_tasks.list_tasks() == []
        finally:
            history.close()
