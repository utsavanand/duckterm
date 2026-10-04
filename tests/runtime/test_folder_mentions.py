"""Owner dispatch preserves scope, idempotency, and correlated replies across restarts."""

import json
import time

import pytest
from tests.runtime.test_folder_view import app, req  # noqa: F401
from tests.runtime.test_session_api import call, enroll

from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def dispatch_request(server, key="first", target=None, recipients=None):
    return {
        "identity": req(server, "GET", "/folders/a/recipients")[1]["identity"],
        "target": target or {"kind": "session", "id": "one"},
        "text": "Check keyboard navigation",
        "request_key": key,
        "recipients": ["one"] if recipients is None else recipients,
    }


def test_scope_auth_and_stale_recipient_review(app):  # noqa: F811
    for key in ["one", "two"]:
        enroll(app.history, key, "a")
    assert req(app, "GET", "/folders/a/recipients", headers={})[0] == 401
    assert req(app, "POST", "/folders/a/dispatch", {}, headers={})[0] == 401
    assert (
        req(app, "GET", "/folders/a/recipients", headers={"authorization": "Bearer no"})[0] == 403
    )
    result = req(app, "GET", "/folders/a/recipients")[1]
    assert {r["session_id"] for r in result["sessions"]} == {"one", "two"}
    assert [r["path"] for r in result["folders"]] == ["a/child"]
    for target, recipients in [
        ({"kind": "session", "id": "outside"}, ["outside"]),
        ({"kind": "folder", "id": "ab"}, ["outside"]),
        ({"kind": "folder", "id": "a/child"}, ["one", "two"]),
    ]:
        assert req(
            app,
            "POST",
            "/folders/a/dispatch",
            dispatch_request(app, target=target, recipients=recipients),
        )[0] in (400, 409)
    assert app.history.session_api.pending_counts() == {}
    request = dispatch_request(app)
    app.history.set_meta("one", group="ab")
    assert req(app, "POST", "/folders/a/dispatch", request)[0] == 409
    assert req(app, "GET", "/folders/a/chat")[1]["messages"] == []


def test_direct_owner_question_retry_reply_and_retirement(app):  # noqa: F811
    credentials = enroll(app.history, "one", "a")
    request = dispatch_request(app)
    status, result = req(app, "POST", "/folders/a/dispatch", request)
    assert status == 200
    message = result["exchange"]["dispatch"]["recipients"][0]
    assert req(app, "POST", "/folders/a/dispatch", request)[1] == result
    assert req(app, "POST", "/folders/a/dispatch", {**request, "text": "different"})[0] == 409
    inbox = call(app.history, credentials, "GET", "/inbox")[1]["messages"]
    assert len(inbox) == 1
    assert inbox[0]["sender_kind"] == "owner" and inbox[0]["requires_reply"] is True
    assert inbox[0]["status"] == "queued"
    assert "1 unread owner message(s)" in app.history.session_api.turn_end_notice("one")
    path = f"/questions/{message['message_id']}"
    assert call(app.history, credentials, "POST", path + "/accept")[1]["status"] == "accepted"
    assert (
        call(
            app.history, credentials, "POST", path + "/answer", {"text": "Navigation verified 🦆"}
        )[0]
        == 200
    )
    # A never-opened reply must be copied before the inbox's seven-day retirement.
    conn = app.history._conn
    old = int(time.time() * 1000) - 9 * 86400000
    conn.execute(
        "UPDATE session_questions SET answered_at = ?, created_at = ? WHERE id = ?",
        (old, old, message["message_id"]),
    )
    conn.commit()
    app.history.session_api._sweep()
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM session_questions WHERE id = ?", (message["message_id"],)
        ).fetchone()[0]
        == 0
    )
    saved = req(app, "GET", "/folders/a/chat")[1]["messages"][0]
    assert saved["dispatch"]["recipients"][0]["answer"] == "Navigation verified 🦆"
    restarted = HistoryStore(app.history.folder_chats.path.parent / "db.sqlite")
    try:
        assert req(Server(history=restarted), "GET", "/folders/a/chat")[1]["messages"] == [saved]
    finally:
        restarted.close()


def test_subfolder_broadcast_review_and_rename(app):  # noqa: F811
    credentials = enroll(app.history, "two", "a")
    request = dispatch_request(app, target={"kind": "folder", "id": "a/child"}, recipients=["two"])
    status, result = req(app, "POST", "/folders/a/dispatch", request)
    assert status == 200
    inbox = call(app.history, credentials, "GET", "/inbox")[1]["messages"]
    assert len(inbox) == 1 and inbox[0]["requires_reply"] is False
    assert req(app, "PATCH", "/folders/a", {"name": "renamed"})[0] == 200
    mid = result["exchange"]["dispatch"]["recipients"][0]["message_id"]
    call(app.history, credentials, "POST", f"/questions/{mid}/answer", {"text": "Still here"})
    assert (
        req(app, "GET", "/folders/renamed/chat")[1]["messages"][0]["dispatch"]["recipients"][0][
            "answer"
        ]
        == "Still here"
    )
    assert req(app, "POST", "/folders/renamed/dispatch", request)[0] == 409
    assert req(app, "DELETE", "/folders/renamed")[0] == 200
    app.history.create_folder("renamed")
    assert req(app, "GET", "/folders/renamed/chat")[1]["messages"] == []


def test_direct_owner_scope_changes_and_peer_rejection(app):  # noqa: F811
    credentials = enroll(app.history, "one", "a")
    other = enroll(app.history, "two", "a")
    result = req(app, "POST", "/folders/a/dispatch", dispatch_request(app))[1]
    mid = result["exchange"]["dispatch"]["recipients"][0]["message_id"]
    with pytest.raises(APIError):
        call(app.history, other, "POST", f"/questions/{mid}/answer", {"text": "forged"})
    assert req(app, "PATCH", "/folders/a", {"name": "renamed"})[0] == 200
    assert call(app.history, credentials, "GET", f"/questions/{mid}")[1]["sender_kind"] == "owner"
    app.history.set_meta("one", group="ab")
    with pytest.raises(APIError):
        call(app.history, credentials, "POST", f"/questions/{mid}/answer", {"text": "moved"})
    assert (
        req(app, "GET", "/folders/renamed/chat")[1]["messages"][0]["dispatch"]["recipients"][0][
            "status"
        ]
        == "cancelled"
    )


def test_broker_retry_after_history_write_failure_and_input_limits(app, monkeypatch):  # noqa: F811
    credentials = enroll(app.history, "one", "a")
    request = dispatch_request(app)
    original = app.history.folder_chats.append
    monkeypatch.setattr(
        app.history.folder_chats,
        "append",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("write failed")),
    )
    assert req(app, "POST", "/folders/a/dispatch", request)[0] == 400
    monkeypatch.setattr(app.history.folder_chats, "append", original)
    assert req(app, "POST", "/folders/a/dispatch", request)[0] == 200
    assert len(call(app.history, credentials, "GET", "/inbox")[1]["messages"]) == 1
    assert req(app, "POST", "/folders/a/dispatch", {**request, "text": "🦆" * 5000})[0] == 413
    assert req(app, "POST", "/folders/a/dispatch", ["invalid"])[0] == 400
    assert "outside" not in json.dumps(req(app, "GET", "/folders/a/chat")[1])
