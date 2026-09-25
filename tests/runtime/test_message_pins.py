"""Pins preserve the chosen message without retargeting rewritten transcripts."""

import copy
import json

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    for key in ("a", "b"):
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "test": True,
                "cwd": str(tmp_path),
            }
        )
    records = [{"id": 5, "role": "assistant", "blocks": [{"type": "text", "text": "Keep this"}]}]

    class Runtime:
        def messages(self, **kwargs):
            return copy.deepcopy(records)

    monkeypatch.setattr("duckterm.server._build_runtime", lambda *args: Runtime())
    server = Server(history=history)
    yield server, {"x-duckterm-token": server.token}, records
    history.purge_test_sessions()
    history.close()


def messages(server, owner, key="a"):
    return dispatch(server, "GET", f"/sessions/{key}/messages", owner)[1]["messages"]


def pin(server, owner, key, session="a"):
    return dispatch(
        server,
        "POST",
        f"/sessions/{session}/pins",
        owner,
        json.dumps({"message_key": key}).encode(),
    )


def test_snapshot_persistence_idempotency_and_unpin_preserve_transcript(scenario, tmp_path):
    server, owner, records = scenario
    original = copy.deepcopy(records)
    key = messages(server, owner)[0]["message_key"]
    status, saved = pin(server, owner, key)
    assert status == 200
    assert pin(server, owner, key) == (status, saved)
    other = HistoryStore(tmp_path / "db.sqlite")
    try:
        assert other.message_pins("a") == [saved["pin"]]
        assert other.message_pins("b") == []
    finally:
        other.close()
    # A restart or transcript disappearance does not destroy a saved pin.
    records.clear()
    assert pin(server, owner, key) == (status, saved)
    assert dispatch(server, "GET", "/sessions/a/pins", owner)[1]["pins"] == [saved["pin"]]
    for _ in range(2):
        assert dispatch(server, "DELETE", f"/sessions/a/pins/{key}", owner)[0] == 200
    records.extend(original)
    assert records == original
    assert messages(server, owner)[0]["message_key"] == key
    assert dispatch(server, "GET", "/sessions/a/pins", owner)[1] == {"pins": []}


def test_append_keeps_key_rewrite_does_not_retarget_and_cross_session_delete(scenario):
    server, owner, records = scenario
    key = messages(server, owner)[0]["message_key"]
    saved = pin(server, owner, key)[1]["pin"]
    records.append({"id": 6, "role": "assistant", "blocks": records[0]["blocks"]})
    current = messages(server, owner)
    assert current[0]["message_key"] == key
    assert current[1]["message_key"] != key
    records[0] = {"id": 5, "role": "assistant", "blocks": [{"type": "text", "text": "Different"}]}
    assert all(m["message_key"] != key for m in messages(server, owner))
    assert dispatch(server, "DELETE", f"/sessions/b/pins/{key}", owner)[0] == 200
    assert server.history.message_pins("a") == [saved]
    assert server.history.delete_session("a")
    assert server.history.message_pins("a") == []


def test_stale_selection_auth_and_input_validation(scenario):
    server, owner, records = scenario
    key = messages(server, owner)[0]["message_key"]
    for method in ("GET", "POST", "DELETE"):
        path = "/sessions/a/pins" + (f"/{key}" if method == "DELETE" else "")
        assert dispatch(server, method, path, {})[0] == 401
        assert dispatch(server, method, path, {"authorization": "Bearer agent"})[0] == 403
    for raw in (b"[]", b"null", b"\xff", b"{}", b'{"message_key": 1}'):
        assert dispatch(server, "POST", "/sessions/a/pins", owner, raw)[0] == 400
    assert dispatch(server, "POST", "/sessions/a/pins", owner, b"x" * 4097)[0] == 413
    assert pin(server, owner, key, session="missing")[0] == 404
    records.clear()
    assert pin(server, owner, key)[0] == 409
    assert server.history.message_pins("a") == []


def test_pin_limit_and_test_cleanup(scenario):
    server, owner, _ = scenario
    key = messages(server, owner)[0]["message_key"]
    for n in range(100):
        server.history.add_message_pin("a", {"message_key": f"{n:064x}", "blocks": []})
    assert pin(server, owner, key)[0] == 409
    # Retry an existing pin even at the limit.
    assert pin(server, owner, "0" * 64)[0] == 200
    assert set(server.history.purge_test_sessions()) == {"a", "b"}
    assert server.history.message_pins("a") == []
