"""Owner broadcasts are durable inbox notices, never terminal input."""

import json
import time
from urllib.parse import quote

import pytest
from tests.runtime.test_session_api import ask, call, dispatch, enroll

from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def scenario(tmp_path):
    history = HistoryStore(tmp_path / "db.sqlite")
    for key, folder in [
        ("a", "work"),
        ("b", "work/nested/deep"),
        ("busy", "work/nested"),
        ("stopped", "work"),
        ("private", "work"),
        ("outside", "workshop"),
        ("ungrouped", ""),
    ]:
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "test": True,
                "runtime": "claude-code",
            }
        )
        history.set_meta(key, name=key, group=folder)
    a, b = enroll(history, "a"), enroll(history, "b")
    enroll(history, "busy")
    enroll(history, "stopped")
    history.set_state("busy", "busy")
    history.set_state("stopped", "stopped")
    history._conn.execute("DELETE FROM session_api_members WHERE session_key = 'private'")
    history._conn.commit()
    server = Server(history=history)
    owner = {"x-duckterm-token": server.token}
    yield history, server, owner, a, b
    history.close()


def send(server, owner, text="Please review the plan", key="first", folder="work"):
    return dispatch(
        server,
        "POST",
        f"/folders/{quote(folder, safe='')}/broadcast",
        owner,
        json.dumps({"text": text, "request_key": key}).encode(),
    )


def test_owner_only_preview_and_nested_fanout(scenario, monkeypatch):
    history, server, owner, a, b = scenario
    monkeypatch.setattr(
        server.orchestrator, "get", lambda *_: pytest.fail("must not access terminals")
    )
    route = "/folders/work/broadcast"
    assert dispatch(server, "GET", route, {})[0] == 401
    assert dispatch(server, "GET", route, a)[0] == 403
    assert send(server, {})[0] == 401
    assert send(server, a)[0] == 403
    preview = dispatch(server, "GET", route, owner)[1]["targets"]
    assert {t["session_id"] for t in preview} == {"a", "b", "busy", "stopped", "private"}
    status, result = send(server, owner)
    assert status == 202
    assert (result["queued"], result["skipped"]) == (4, 1)
    assert {r["session_id"] for r in result["results"] if r["status"] == "queued"} == {
        "a",
        "b",
        "busy",
        "stopped",
    }
    assert history.session_api.inbox("outside", owner=True)["messages"] == []
    assert history.session_api.inbox("ungrouped", owner=True)["messages"] == []
    message = call(history, b, "GET", "/inbox")[1]["messages"][0]
    assert (message["sender"], message["sender_kind"], message["kind"]) == (
        "owner",
        "owner",
        "broadcast",
    )
    assert message["requires_reply"] is False
    assert message["status"] == "read"
    assert message["expires_at"] == 0


def test_durable_idempotent_batch_and_no_peer_budget(scenario, tmp_path, monkeypatch):
    history, server, owner, a, _ = scenario
    _, first = send(server, owner)
    assert send(server, owner)[1] == first
    assert send(server, owner, text="different")[0] == 409
    for n in range(25):
        assert send(server, owner, key=f"batch-{n}")[0] == 202
    assert ask(history, a)["kind"] == "question"
    future = time.time() + 3600
    monkeypatch.setattr("duckterm.core.session_api.time.time", lambda: future)
    reopened = HistoryStore(tmp_path / "db.sqlite")
    try:
        page = reopened.session_api.inbox("b", owner=True)["messages"]
        assert len([m for m in page if m["kind"] == "broadcast"]) == 26
        assert all(m["status"] == "queued" for m in page)
    finally:
        reopened.close()
    monkeypatch.setattr("duckterm.core.session_api.time.time", lambda: future + 8 * 86400)
    page = history.session_api.inbox("b", owner=True)["messages"]
    assert [m["kind"] for m in page] == ["question"]


def test_owner_notice_reminder_read_and_optional_reply(scenario):
    history, server, owner, a, b = scenario
    send(server, owner)
    raw = {"event_type": "Stop", "stop_hook_active": False}
    output = server._inbox_hook_output(raw, "b")
    assert "1 unread owner" in output["hookSpecificOutput"]["additionalContext"]
    assert server._inbox_hook_output(raw, "b") is None
    history.session_api.inbox("b", owner=True)
    assert history.session_api.pending_counts()["b"] == 1
    message = call(history, b, "GET", "/inbox")[1]["messages"][0]
    assert "b" not in history.session_api.pending_counts()
    with pytest.raises(APIError) as exc:
        call(history, b, "POST", f"/questions/{message['id']}/accept")
    assert exc.value.status == 400
    with pytest.raises(APIError) as exc:
        call(history, a, "GET", f"/questions/{message['id']}")
    assert exc.value.status == 404
    reply = call(history, b, "POST", f"/questions/{message['id']}/answer", {"text": "Done"})[1]
    assert reply["answer"] == "Done"
    assert reply["status"] == "answered"
    send(server, owner, key="second")
    history.set_state("b", "waiting")
    assert server._inbox_hook_output(raw, "b") is None
    history.set_state("b", "idle")
    assert (
        "1 unread owner"
        in server._inbox_hook_output(raw, "b")["hookSpecificOutput"]["additionalContext"]
    )


def test_preview_validation_and_literal_nested_folder(scenario):
    history, server, owner, _, _ = scenario
    history.set_meta("b", group="work/100%_done/deep")
    assert send(server, owner, folder="work/100%_done")[1]["queued"] == 1
    assert send(server, owner, folder="wor")[0] == 404
    assert send(server, owner, text=" ")[0] == 400
    assert send(server, owner, text="x" * 16385)[0] == 413
    assert dispatch(server, "POST", "/folders/work/broadcast", owner, b"[]")[0] == 400
    assert dispatch(server, "POST", "/folders/work/broadcast", owner, b"{")[0] == 400
    assert dispatch(server, "POST", "/folders/work/broadcast", owner, b"\xff")[0] == 400


def test_scope_changes_preserve_folder_move_but_revoke_individual_move(scenario):
    history, server, owner, _, _ = scenario
    send(server, owner)
    history.move_folder("work", "renamed")
    assert history.session_api.inbox("b", owner=True)["messages"][0]["status"] == "queued"
    history.set_meta("b", group="elsewhere")
    assert history.session_api.inbox("b", owner=True)["messages"][0]["status"] == "cancelled"
    assert "b" not in history.session_api.pending_counts()


def test_failed_fanout_rolls_back_every_recipient(scenario):
    import sqlite3

    history, server, owner, _, _ = scenario
    history._conn.execute(
        "CREATE TRIGGER reject_second BEFORE INSERT ON session_questions "
        "WHEN NEW.recipient = 'b' BEGIN SELECT RAISE(ABORT, 'test failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        history.session_api.broadcast("work", {"text": "Atomic batch", "request_key": "atomic"})
    assert history._conn.execute("SELECT COUNT(*) FROM session_questions").fetchone()[0] == 0
    assert history._conn.execute("SELECT COUNT(*) FROM session_broadcasts").fetchone()[0] == 0
    history._conn.execute("DROP TRIGGER reject_second")
    assert send(server, owner, text="Atomic batch", key="atomic")[1]["queued"] == 4


def test_existing_database_adds_kind_without_losing_peer_messages(scenario, tmp_path):
    history, _, _, a, _ = scenario
    question = ask(history, a)
    history._conn.execute("ALTER TABLE session_questions DROP COLUMN kind")
    history._conn.commit()
    reopened = HistoryStore(tmp_path / "db.sqlite")
    try:
        message = reopened.session_api.inbox("b", owner=True)["messages"][0]
        assert message["id"] == question["id"]
        assert message["kind"] == "question"
        assert message["requires_reply"] is True
    finally:
        reopened.close()
