"""Owner Focus pins persist, retain stopped sessions, and never exceed three."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    for key in ("a", "b", "c", "d"):
        history.record(
            {"_id": key, "_ts": 1, "event_type": "SessionStart", "session_key": key, "test": True}
        )
        history.set_meta(key, group=f"folder/{key}")
    server = Server(history=history)
    yield server, {"x-duckterm-token": server.token}
    history.purge_test_sessions()
    history.close()


def pin(server, owner, key, pinned=True):
    return dispatch(
        server,
        "PUT",
        f"/sessions/{key}/focus-pin",
        owner,
        json.dumps({"pinned": pinned}).encode(),
    )


def pinned_keys(history):
    return {s["session_key"] for s in history.sessions() if s["pinned"]}


def test_cap_no_swap_idempotency_and_delete_frees_slot(scenario):
    server, owner = scenario
    for key in "abc":
        assert pin(server, owner, key) == (200, {"pinned": True})
    assert pin(server, owner, "a")[0] == 200
    assert pin(server, owner, "d") == (409, {"error": "Unpin one first"})
    assert pinned_keys(server.history) == set("abc")
    assert server.history.delete_session("b")
    assert pin(server, owner, "d")[0] == 200
    assert pinned_keys(server.history) == set("acd")
    assert pin(server, owner, "a", False) == (200, {"pinned": False})
    assert pin(server, owner, "a", False)[0] == 200
    assert pinned_keys(server.history) == set("cd")


def test_persistence_lifecycle_and_existing_session_payload(scenario, tmp_path):
    server, owner = scenario
    for key, state in (("a", "stopped"), ("b", "archived")):
        assert pin(server, owner, key)[0] == 200
        server.history.record(
            {
                "_id": key + "-end",
                "_ts": 2,
                "session_key": key,
                "event_type": "SessionEnd",
                "lifecycle": state,
                "test": True,
            }
        )
    reopened = HistoryStore(tmp_path / "db.sqlite")
    try:
        assert pinned_keys(reopened) == set("ab")
        assert reopened.session("a")["state"] == "stopped"
        assert reopened.session("b")["state"] == "archived"
        status, payload = dispatch(Server(history=reopened), "GET", "/sessions", {})
        assert status == 200
        assert {s["session_key"] for s in payload["sessions"] if s["pinned"]} == set("ab")
    finally:
        reopened.close()


def test_owner_auth_validation_and_missing_session(scenario):
    server, owner = scenario
    assert pin(server, {}, "a")[0] == 401
    assert pin(server, {"authorization": "Bearer agent"}, "a")[0] == 403
    for raw in (b"[]", b"null", b"\xff", b"{}", b'{"pinned": 1}', b'{"pinned": "false"}'):
        assert dispatch(server, "PUT", "/sessions/a/focus-pin", owner, raw)[0] == 400
    assert dispatch(server, "PUT", "/sessions/a/focus-pin", owner, b"x" * 4097)[0] == 413
    assert pin(server, owner, "missing")[0] == 404
    assert pinned_keys(server.history) == set()


def test_concurrent_connections_cannot_take_the_same_last_slot(scenario, tmp_path):
    server, owner = scenario
    for key in "ab":
        pin(server, owner, key)
    # Independent connections mimic two writers racing for the final slot.
    ready = Barrier(2)

    def race(index):
        # Each independent connection is created, used and closed by its owner.
        store = HistoryStore(tmp_path / "db.sqlite")
        try:
            ready.wait(timeout=5)
            try:
                return store.set_pinned("cd"[index], True)
            except ValueError as exc:
                assert str(exc) == "Unpin one first"
                return False
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(race, range(2))) == [False, True]
    assert len(pinned_keys(server.history)) == 3
    assert set("ab") <= pinned_keys(server.history)


def test_older_database_migrates_with_no_pins(tmp_path):
    path = tmp_path / "old.sqlite"
    store = HistoryStore(path)
    store.record({"_id": "old", "_ts": 1, "session_key": "old", "test": True})
    store.close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE sessions DROP COLUMN pinned")
        conn.execute("PRAGMA user_version=3")
    reopened = HistoryStore(path)
    try:
        assert reopened.session("old")["pinned"] == 0
        assert reopened.set_pinned("old", True)
    finally:
        reopened.purge_test_sessions()
        reopened.close()
