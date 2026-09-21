"""Real persistence and dispatch checks for session-scoped collaboration."""

import asyncio
import json
from pathlib import Path

import pytest

from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    history = HistoryStore(tmp_path / "db.sqlite")
    for key, folder in [("a", "work/backend/a"), ("b", "work/backend/b"), ("c", "other")]:
        history.record({"_id": key, "_ts": 1, "event_type": "SessionStart", "session_key": key})
        history.set_meta(key, name=f"Session {key}", group=folder, notes="private notes")
        history.set_intention(key, "private initial prompt")
    return history


def enroll(store: HistoryStore, key: str, root: str = "work") -> dict[str, str]:
    result = store.session_api.enroll(key, {"root": root, "purpose": "Published purpose"})
    return {"authorization": f"Bearer {result['token']}"}


def call(store: HistoryStore, headers: dict, method: str, path: str, body: object = None) -> tuple:
    return store.session_api.handle(
        method, "/api/v1/session" + path, headers, json.dumps(body or {}).encode()
    )


def ask(store: HistoryStore, headers: dict, idem: str = "request-1") -> dict:
    status, result = call(
        store,
        {**headers, "idempotency-key": idem},
        "POST",
        "/questions",
        {"target_session_id": "b", "question": "What contract should I use?"},
    )
    assert status in (200, 202)
    return result


def test_discovery_scopes_and_private_fields(store: HistoryStore) -> None:
    a = enroll(store, "a")
    enroll(store, "b")
    enroll(store, "c", "other")
    assert call(store, a, "GET", "/peers")[1]["sessions"] == []
    peers = call(store, a, "GET", "/peers?scope=parent")[1]["sessions"]
    assert [p["session_id"] for p in peers] == ["b"]
    assert peers[0]["name"] == "Session b"
    assert "private" not in json.dumps(peers)
    assert "token_hash" not in json.dumps(peers)
    assert call(store, a, "GET", "/peers?scope=grandparent")[1]["sessions"] == peers
    narrow = enroll(store, "a", "work/backend/a")
    with pytest.raises(APIError) as error:
        call(store, narrow, "GET", "/peers?scope=parent")
    assert error.value.status == 403


def test_exchange_and_restart(store: HistoryStore, tmp_path: Path) -> None:
    a, b = enroll(store, "a"), enroll(store, "b")
    question = ask(store, a)
    assert question["status"] == "queued"
    assert ask(store, a)["id"] == question["id"]
    inbox = call(store, b, "GET", "/inbox")[1]["messages"]
    assert inbox[0]["sender_name"] == "Session a"
    path = f"/questions/{question['id']}"
    call(store, b, "POST", path + "/accept")
    text = "Use this contract:\n\n" + "Full Unicode reply — 🦆\n" * 2000
    call(store, b, "POST", path + "/answer", {"text": text})
    store.close()
    reopened = HistoryStore(tmp_path / "db.sqlite")
    assert call(reopened, a, "GET", path)[1]["answer"] == text
    assert call(reopened, b, "POST", path + "/answer", {"text": text})[0] == 200
    with pytest.raises(APIError) as error:
        call(reopened, b, "POST", path + "/answer", {"text": "different"})
    assert error.value.status == 409
    reopened.close()


def test_forged_sender_wrong_recipient_and_outside_root(store: HistoryStore) -> None:
    a, b, c = enroll(store, "a"), enroll(store, "b"), enroll(store, "c", "other")
    question = ask(store, a)
    path = f"/questions/{question['id']}"
    for headers, method, suffix, body in [
        (c, "GET", "", {}),
        (c, "POST", "/answer", {"text": "fake"}),
        (a, "POST", "/answer", {"text": "fake"}),
        (b, "POST", "/cancel", {}),
    ]:
        with pytest.raises(APIError) as error:
            call(store, headers, method, path + suffix, body)
        assert error.value.status == 404
    with pytest.raises(APIError) as error:
        call(
            store,
            {**a, "idempotency-key": "forged"},
            "POST",
            "/questions",
            {"sender": "b", "target_session_id": "b", "question": "forged"},
        )
    assert error.value.status == 400
    with pytest.raises(APIError) as error:
        call(
            store,
            {**a, "idempotency-key": "outside"},
            "POST",
            "/questions",
            {"target_session_id": "c", "question": "hello"},
        )
    assert error.value.status == 404


@pytest.mark.parametrize("action", ["stop", "end", "delete"])
def test_revocation_is_permanent_and_cancels_pending(store: HistoryStore, action: str) -> None:
    a = enroll(store, "a")
    b = enroll(store, "b")
    ask(store, a)
    if action == "stop":
        store.set_state("a", "stopped")
        store.set_state("a", "busy")
    elif action == "end":
        store.record({"_id": "end", "_ts": 2, "event_type": "SessionEnd", "session_key": "a"})
        store.set_state("a", "busy")
    elif action == "delete":
        store.delete_session("a")
    with pytest.raises(APIError) as error:
        call(store, a, "GET", "/self")
    assert error.value.status == 401
    visible = call(store, b, "GET", "/inbox")[1]["messages"]
    assert not visible or visible[0]["status"] == "cancelled"


def test_deadlines_cancel_and_idempotency_conflict(store: HistoryStore, monkeypatch) -> None:
    monkeypatch.setattr("duckterm.core.session_api.time.time", lambda: 1000)
    a, b = enroll(store, "a"), enroll(store, "b")
    question = ask(store, a)
    with pytest.raises(APIError) as error:
        call(
            store,
            {**a, "idempotency-key": "request-1"},
            "POST",
            "/questions",
            {"target_session_id": "b", "question": "different"},
        )
    assert error.value.status == 409
    monkeypatch.setattr("duckterm.core.session_api.time.time", lambda: 1400)
    path = f"/questions/{question['id']}"
    assert call(store, a, "GET", path)[1]["status"] == "expired"
    with pytest.raises(APIError) as error:
        call(store, b, "POST", path + "/answer", {"text": "too late"})
    assert error.value.status == 409
    question = ask(store, a, "second")
    path = f"/questions/{question['id']}"
    assert call(store, a, "POST", path + "/cancel")[1]["status"] == "cancelled"
    assert call(store, a, "POST", path + "/cancel")[0] == 200


def test_enrollment_collision_rolls_back_and_root_requires_ancestor(store: HistoryStore) -> None:
    a = enroll(store, "a")
    b = enroll(store, "b")
    with pytest.raises(APIError) as error:
        store.session_api.enroll("b", {"root": "work", "api_name": "a"})
    assert error.value.status == 409
    assert call(store, b, "GET", "/self")[0] == 200
    for root in ["wor", "work/backend/bad", "", "work/../other"]:
        with pytest.raises(APIError):
            store.session_api.enroll("a", {"root": root})
    assert call(store, a, "GET", "/self")[0] == 200


def test_owner_inbox_cursor_and_expired_records(store: HistoryStore, monkeypatch) -> None:
    a = enroll(store, "a")
    b = enroll(store, "b")
    now = 1000
    monkeypatch.setattr("duckterm.core.session_api.time.time", lambda: now)
    for i in range(55):
        now += 61
        q = ask(store, a, str(i))
        call(store, b, "POST", f"/questions/{q['id']}/answer", {"text": str(i)})
    first = store.session_api.inbox("b", owner=True)
    second = store.session_api.inbox("b", owner=True, before=first["next_cursor"])
    assert len(first["messages"]) == 50
    assert len(second["messages"]) == 5
    assert second["next_cursor"] is None
    assert len({m["id"] for m in first["messages"] + second["messages"]}) == 55


class Writer:
    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        pass


def dispatch(server: Server, method: str, path: str, headers: dict, body: bytes = b"") -> tuple:
    writer = Writer()

    async def run() -> None:
        await server._dispatch(method, path, asyncio.StreamReader(), writer, headers, body)

    asyncio.run(run())
    head, data = writer.data.split(b"\r\n\r\n", 1)
    return int(head.split()[1]), json.loads(data)


def test_http_auth_and_owner_inbox(store: HistoryStore) -> None:
    server = Server(history=store)
    a = enroll(store, "a")
    enroll(store, "b")
    ask(store, a)
    assert dispatch(server, "GET", "/api/v1/session/self", {})[0] == 401
    assert dispatch(server, "GET", "/api/v1/session/self", a)[0] == 200
    assert dispatch(server, "GET", "/sessions", a)[0] == 403
    assert dispatch(server, "GET", "/sessions/b/inbox", {})[0] == 401
    owner = {"x-duckterm-token": server.token}
    status, inbox = dispatch(server, "GET", "/sessions/b/inbox", owner)
    assert status == 200 and inbox["messages"][0]["sender"] == "a"
    assert dispatch(server, "GET", "/sessions/b/inbox?before=bad", owner)[0] == 400
    assert dispatch(server, "GET", "/sessions/b/inbox?before=" + "9" * 100, owner)[0] == 400
    assert dispatch(server, "POST", "/sessions/a/collaboration", a, b"{}")[0] == 403
    assert dispatch(server, "POST", "/sessions/a/collaboration", owner, b"[]")[0] == 400
    assert dispatch(server, "PATCH", "/api/v1/session/self", a, b"[]")[0] == 400


def test_payload_limits_and_rate_limit(store: HistoryStore) -> None:
    a, b = enroll(store, "a"), enroll(store, "b")
    with pytest.raises(APIError) as error:
        call(
            store,
            {**a, "idempotency-key": "large"},
            "POST",
            "/questions",
            {"target_session_id": "b", "question": "x" * 16385},
        )
    assert error.value.status == 413
    for i in range(10):
        question = ask(store, a, str(i))
        call(store, b, "POST", f"/questions/{question['id']}/answer", {"text": "done"})
    with pytest.raises(APIError) as error:
        ask(store, a, "limited")
    assert error.value.status == 429


def token_for(store: HistoryStore, key: str) -> dict[str, str]:
    from duckterm.helpers.session_credentials import credential_path

    value = json.loads(credential_path(key, store.session_api.credential_dir).read_text())
    return {"authorization": "Bearer " + value["token"]}


def test_automatic_enrollment_and_reopen_are_idempotent(
    store: HistoryStore, tmp_path: Path
) -> None:
    a = token_for(store, "a")
    assert call(store, a, "GET", "/self")[1]["root"] == "work"
    assert [
        s["session_id"] for s in call(store, a, "GET", "/peers?scope=parent")[1]["sessions"]
    ] == ["b"]
    assert store.session_api.backfill() == 0
    store.close()
    reopened = HistoryStore(tmp_path / "db.sqlite")
    assert token_for(reopened, "a") == a
    assert call(reopened, a, "GET", "/self")[0] == 200
    reopened.close()


def test_backfill_old_sessions_and_private_ungrouped(store: HistoryStore) -> None:
    with store._conn:
        store._conn.execute("DELETE FROM session_api_members")
        store._conn.execute("UPDATE sessions SET grp = NULL WHERE session_key = 'c'")
    assert store.session_api.backfill() == 3
    c = token_for(store, "c")
    assert call(store, c, "GET", "/self")[1]["root"] == ""
    assert call(store, c, "GET", "/peers?scope=grandparent")[1]["sessions"] == []
    with pytest.raises(APIError):
        call(
            store,
            {**c, "idempotency-key": "x"},
            "POST",
            "/questions",
            {"target_session_id": "b", "question": "outside"},
        )


def test_folder_moves_preserve_credentials_and_update_discovery(store: HistoryStore) -> None:
    a, b = token_for(store, "a"), token_for(store, "b")
    q = ask(store, a)
    store.move_folder("work", "renamed")
    assert call(store, a, "GET", "/self")[1]["folder"] == "renamed/backend/a"
    assert call(store, a, "GET", "/self")[1]["root"] == "renamed"
    assert call(store, b, "POST", f"/questions/{q['id']}/answer", {"text": "still here"})[0] == 200
    q = ask(store, a, "pending")
    store.set_meta("b", group="other/moved")
    assert token_for(store, "b") == b
    assert call(store, b, "GET", "/self")[1]["root"] == "other"
    assert call(store, a, "GET", "/peers?scope=grandparent")[1]["sessions"] == []
    assert store.session_api.inbox("b", owner=True)["messages"][0]["status"] == "cancelled"
    with pytest.raises(APIError):
        call(store, a, "GET", f"/questions/{q['id']}")


def test_card_follows_progress_and_metadata_without_exposing_prompt(store: HistoryStore) -> None:
    a = token_for(store, "a")
    store.set_meta("a", name="Authentication")
    initial = call(store, a, "GET", "/self")[1]
    assert initial["purpose"] == "Authentication"
    store.set_progress(
        "a",
        json.dumps(
            {
                "summary": "Token refresh implemented; validating expiration.",
                "next_actions": ["Test clock skew"],
                "deliverables": ["Refresh endpoint"],
            }
        ),
        initial["updated_at"] + 10,
    )
    card = call(store, a, "GET", "/self")[1]
    assert card["purpose"] == "Token refresh implemented; validating expiration."
    assert card["next_actions"] == ["Test clock skew"]
    assert "private initial prompt" not in json.dumps(card)
    assert card["updated_at"] > initial["updated_at"]


def test_pending_counts_survive_reading_and_clear_on_answer(store: HistoryStore) -> None:
    a, b = token_for(store, "a"), token_for(store, "b")
    q = ask(store, a)
    assert store.session_api.pending_counts() == {"b": 1}
    store.session_api.inbox("b", owner=True)
    call(store, b, "POST", f"/questions/{q['id']}/accept")
    assert store.session_api.pending_counts() == {"b": 1}
    call(store, b, "POST", f"/questions/{q['id']}/answer", {"text": "complete"})
    assert store.session_api.pending_counts() == {}


def test_moving_entire_automatic_group_joins_destination_root(store: HistoryStore) -> None:
    a, b = token_for(store, "a"), token_for(store, "b")
    question = ask(store, a)
    store.move_folder("work", "other/work")
    assert call(store, a, "GET", "/self")[1]["root"] == "other"
    peers = call(store, a, "GET", "/peers?scope=shared_root")[1]["sessions"]
    assert {peer["session_id"] for peer in peers} == {"b", "c"}
    assert (
        call(store, b, "POST", f"/questions/{question['id']}/answer", {"text": "same thread"})[0]
        == 200
    )


def test_folder_names_with_sql_wildcards_do_not_move_other_trees(store: HistoryStore) -> None:
    store.set_meta("a", group="project_one/child")
    store.set_meta("b", group="projectXone/child")
    a, b = token_for(store, "a"), token_for(store, "b")
    store.move_folder("project_one", "moved")
    assert call(store, a, "GET", "/self")[1]["root"] == "moved"
    assert call(store, b, "GET", "/self")[1]["folder"] == "projectXone/child"
    store.delete_folder("moved")
    assert call(store, a, "GET", "/self")[1]["root"] == ""
    assert call(store, b, "GET", "/self")[1]["folder"] == "projectXone/child"
