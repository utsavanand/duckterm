"""A reviewed summary is durable; only actual broker delivery checkpoints the parent."""

import json
from types import SimpleNamespace

import pytest
from tests.runtime.test_session_api import dispatch, enroll

from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_ORACLE", "off")
    history = HistoryStore(tmp_path / "history.sqlite")
    for key in ("parent", "child"):
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "session_key": key,
                "event_type": "SessionStart",
                "test": True,
                "runtime": "claude-code",
                "parent_session_key": "parent" if key == "child" else None,
            }
        )
        history.set_meta(key, name=key, group="work")
        enroll(history, key)
    history._conn.execute("UPDATE sessions SET launched = 1")
    history._conn.commit()
    server = Server(history=history)
    monkeypatch.setattr(server, "_maybe_refresh_progress", lambda key: None)
    supervisor = SimpleNamespace(running=True)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: supervisor)

    async def stop(key):
        supervisor.running = False
        return True

    monkeypatch.setattr(server.orchestrator, "stop", stop)
    yield history, server, {"x-duckterm-token": server.token}
    history.close()


def send(rig, unique="one", keep=False, summary="  Summary — 🦆\n\nExact trailing space. \n"):
    _, server, auth = rig
    return dispatch(
        server,
        "POST",
        "/sessions/child/merge",
        auth,
        json.dumps({"summary": summary, "keepOpen": keep, "requestKey": unique}).encode(),
    )


def test_exact_text_idempotency_close_and_late_events(rig):
    h, server, auth = rig
    status, record = send(rig)
    assert status == 200, record
    assert record["checkpoint"] == "pending"
    assert h.checkpoints("parent") == []
    assert h.session("child")["state"] == "merged"
    assert h.session("child")["merged_into"] == "parent"
    assert send(rig)[1] == record
    note = h._conn.execute(
        "SELECT question FROM session_questions WHERE sender = 'owner'"
    ).fetchall()
    assert len(note) == 1 and note[0][0] == record["summary"]
    assert send(rig, keep=True)[0] == 409
    for event in ("SessionStart", "PostToolUse", "SessionEnd"):
        h.record({"_id": event, "_ts": 10, "session_key": "child", "event_type": event})
        assert h.session("child")["state"] == "merged"
    assert dispatch(server, "POST", "/sessions/child/resume", auth)[0] == 400
    assert dispatch(server, "POST", "/sessions/child/restart", auth, b"{}")[0] == 409
    assert len(h.fork_merges.list("child", server._can_pin)) == 1
    assert len(h.fork_merges.list("parent", server._can_pin)) == 1


def test_delivery_checkpoints_once_and_recovery(rig):
    h, server, _ = rig
    record = send(rig, keep=True)[1]
    mid = h._conn.execute("SELECT message_id FROM fork_merges").fetchone()[0]
    server._delivered("parent", [mid])
    server._delivered("parent", [mid])
    cps = h.checkpoints("parent")
    assert len(cps) == 1 and cps[0]["summary"] == record["summary"]
    assert h.fork_merges.status("child", record["id"], server._can_pin)["checkpoint"] == "created"
    send(rig, unique="two", keep=True, summary="Second merge")
    mid2 = h._conn.execute("SELECT message_id FROM fork_merges WHERE id LIKE '%:two'").fetchone()[0]
    # Crash after durable broker delivery, before the checkpoint callback.
    h.session_api.mark_delivered([mid2])
    h.fork_merges.refresh_checkpoints()
    assert len(h.checkpoints("parent")) == 2
    assert h.session("child")["state"] != "merged"


def test_cancelled_or_undelivered_notes_never_checkpoint(rig):
    h, server, auth = rig
    record = send(rig, keep=True)[1]
    assert h.checkpoints("parent") == []
    dispatch(server, "DELETE", "/broadcasts/" + record["id"], auth)
    status = h.fork_merges.status("child", record["id"], server._can_pin)
    assert status["delivery"] == "cancelled" and status["checkpoint"] == "not created"
    assert h.checkpoints("parent") == []


def test_prepared_retry_survives_broker_crash_without_duplicate(rig, monkeypatch):
    h, server, _ = rig
    original = h.session_api.owner_message

    def fail_after_save(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("crash after enqueue")

    monkeypatch.setattr(h.session_api, "owner_message", fail_after_save)
    assert send(rig)[0] == 500
    assert send(rig, keep=True)[0] == 409
    monkeypatch.setattr(h.session_api, "owner_message", original)
    assert send(rig)[0] == 200
    assert (
        h._conn.execute("SELECT count(*) FROM session_questions WHERE sender = 'owner'").fetchone()[
            0
        ]
        == 1
    )


def test_deleted_parent_and_unsupported_runtime(rig):
    h, server, auth = rig
    h._conn.execute("UPDATE sessions SET runtime = 'generic' WHERE session_key = 'parent'")
    h._conn.commit()
    preview = dispatch(server, "GET", "/sessions/child/merge", auth)[1]
    assert preview["parent"]["priorityDelivery"] is False
    assert send(rig, keep=True)[1]["delivery"] == "inbox only"
    h.delete_session("parent")
    preview = dispatch(server, "GET", "/sessions/child/merge", auth)[1]
    assert not preview["allowed"] and preview["parent"] is None
    assert h.session("child")["merged_into"] == "parent"
    assert h.fork_merges.list("child", server._can_pin)[0]["parentDeleted"] is True


def test_owner_only_and_invalid_requests(rig):
    h, server, _ = rig
    for method, suffix in (("GET", "merge"), ("GET", "merges"), ("POST", "merge")):
        assert dispatch(server, method, "/sessions/child/" + suffix, {}, b"{}")[0] == 401
    for req in (
        {},
        {"summary": "a", "keepOpen": "false", "requestKey": "x"},
        {"summary": "a", "keepOpen": False, "requestKey": "../x"},
    ):
        with pytest.raises(APIError):
            h.fork_merges.prepare("child", req, server._can_pin)
    assert h._conn.execute("SELECT count(*) FROM fork_merges").fetchone()[0] == 0


def test_stop_failure_retries_same_note_and_blocks_resume(rig, monkeypatch):
    h, server, auth = rig

    async def fail(key):
        raise OSError("stop failed")

    monkeypatch.setattr(server.orchestrator, "stop", fail)
    assert send(rig)[0] == 500
    assert dispatch(server, "POST", "/sessions/child/resume", auth)[0] == 400
    assert dispatch(server, "POST", "/sessions/child/restart", auth, b"{}")[0] == 409

    async def stop(key):
        return True

    monkeypatch.setattr(server.orchestrator, "stop", stop)
    assert send(rig)[0] == 200
    assert h.session("child")["state"] == "merged"
