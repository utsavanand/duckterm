"""Priority folder dispatch keeps exact sends and durable history retry-safe."""

import time
import urllib.parse

import pytest
from tests.runtime.test_folder_mentions import dispatch_request
from tests.runtime.test_folder_view import app, req  # noqa: F401
from tests.runtime.test_session_api import call, enroll

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def everyone(server, key="priority"):
    return {
        **dispatch_request(server, key, {"kind": "folder", "id": "a"}, ["one", "two"]),
        "priority": True,
        "text": "  Exact owner edit — café 🦆\nSecond line.\n",
    }


def test_priority_everyone_status_cancel_and_retained_history(app, monkeypatch):  # noqa: F811
    creds = {key: enroll(app.history, key, "a") for key in ("one", "two")}
    monkeypatch.setattr(app, "_can_pin", lambda key: key == "one")
    wake = []
    monkeypatch.setattr(app, "_oracle_soon", lambda: wake.append(True))
    request = everyone(app)
    code, body = req(app, "POST", "/folders/a/dispatch", request)
    assert code == 200
    assert wake == [True]
    exchange = body["exchange"]
    delivery = exchange["dispatch"]
    assert exchange["q"] == request["text"]
    assert delivery["priority"] is True
    assert delivery["delivery_key"] != delivery["request_key"]
    mids = {r["session_id"]: r["message_id"] for r in delivery["recipients"]}
    for key in creds:
        inbox = call(app.history, creds[key], "GET", "/inbox")[1]["messages"]
        assert len(inbox) == 1
        assert inbox[0]["question"] == request["text"]
        assert inbox[0]["priority"] is True
    path = "/broadcasts/" + urllib.parse.quote(delivery["delivery_key"], safe="")
    statuses = req(app, "GET", path)[1]["recipients"]
    assert {r["session_id"]: r["status"] for r in statuses} == {
        "one": "pending next turn",
        "two": "inbox only",
    }
    assert req(app, "GET", path, headers={})[0] == 401
    assert (
        call(
            app.history, creds["one"], "POST", f"/questions/{mids['one']}/answer", {"text": "Done"}
        )[0]
        == 200
    )
    cancelled = req(app, "DELETE", path)[1]["recipients"]
    assert {r["session_id"]: r["status"] for r in cancelled} == {
        "one": "acknowledged",
        "two": "cancelled",
    }
    saved = req(app, "GET", "/folders/a/chat")[1]["messages"][0]
    assert saved["dispatch"]["delivery_status"] == {
        mids["one"]: "acknowledged",
        mids["two"]: "cancelled",
    }
    assert saved["dispatch"]["recipients"][0]["status"] == "answered"
    # Retired broker records must not erase the independently saved conversation.
    old = int(time.time() * 1000) - 9 * 86400000
    app.history._conn.execute(
        "UPDATE session_questions SET created_at = ?, answered_at = ?", (old, old)
    )
    app.history._conn.commit()
    app.history.session_api._sweep()
    assert req(app, "GET", path)[0] == 404
    saved = req(app, "GET", "/folders/a/chat")[1]["messages"][0]
    assert saved["q"] == request["text"]
    assert saved["dispatch"]["delivery_status"] == {
        mids["one"]: "acknowledged",
        mids["two"]: "cancelled",
    }
    assert saved["dispatch"]["recipients"][0]["answer"] == "Done"
    restarted = HistoryStore(app.history.folder_chats.path.parent / "db.sqlite")
    try:
        assert req(Server(history=restarted), "GET", "/folders/a/chat")[1]["messages"] == [saved]
    finally:
        restarted.close()


def test_history_failure_retry_and_priority_conflict(app, monkeypatch):  # noqa: F811
    creds = {key: enroll(app.history, key, "a") for key in ("one", "two")}
    request = everyone(app)
    original = app.history.folder_chats.append

    def fail(*args, **kwargs):
        raise ValueError("injected history write failure after enqueue")

    monkeypatch.setattr(app.history.folder_chats, "append", fail)
    assert req(app, "POST", "/folders/a/dispatch", request)[0] == 400
    assert req(app, "POST", "/folders/a/dispatch", {**request, "priority": False})[0] == 409
    monkeypatch.setattr(app.history.folder_chats, "append", original)
    first = req(app, "POST", "/folders/a/dispatch", request)
    assert first[0] == 200
    assert req(app, "POST", "/folders/a/dispatch", request) == first
    assert req(app, "POST", "/folders/a/dispatch", {**request, "priority": False})[0] == 409
    assert req(app, "POST", "/folders/a/dispatch", {**request, "text": "edited"})[0] == 409
    assert len(req(app, "GET", "/folders/a/chat")[1]["messages"]) == 1
    for credential in creds.values():
        assert len(call(app.history, credential, "GET", "/inbox")[1]["messages"]) == 1


@pytest.mark.parametrize("bad", [1, "true", None, []])
def test_priority_is_owner_only_and_strict_boolean(app, bad):  # noqa: F811
    credential = enroll(app.history, "one", "a")
    enroll(app.history, "two", "a")
    request = everyone(app)
    assert req(app, "POST", "/folders/a/dispatch", request, headers=credential)[0] in (401, 403)
    assert req(app, "POST", "/folders/a/dispatch", {**request, "priority": bad})[0] == 400
    assert app.history.session_api.pending_counts() == {}


def test_old_clients_remain_non_priority_and_cached_flip_conflicts(app):  # noqa: F811
    credential = enroll(app.history, "one", "a")
    request = dispatch_request(app)
    assert req(app, "POST", "/folders/a/dispatch", request)[0] == 200
    assert req(app, "POST", "/folders/a/dispatch", {**request, "priority": False})[0] == 200
    assert req(app, "POST", "/folders/a/dispatch", {**request, "priority": True})[0] == 409
    message = call(app.history, credential, "GET", "/inbox")[1]["messages"][0]
    assert message["kind"] == "question" and message["priority"] is False


@pytest.mark.parametrize("key", ["has space", "slash/key", "percent%key", "café", "line\nbreak"])
def test_unroutable_delivery_key_is_rejected_before_enqueue(app, key):  # noqa: F811
    credential = enroll(app.history, "one", "a")
    result = req(app, "POST", "/folders/a/dispatch", everyone(app, key))
    assert result[0] == 400
    assert "request_key" in result[1]["error"]
    assert call(app.history, credential, "GET", "/inbox")[1]["messages"] == []
    assert req(app, "GET", "/folders/a/chat")[1]["messages"] == []
