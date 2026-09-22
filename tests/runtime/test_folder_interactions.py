"""Folder history includes both directions without leaking another folder's data."""

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from tests.runtime.test_session_api import ask, call, dispatch, enroll

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def store(tmp_path: Path) -> Iterator[HistoryStore]:
    history = HistoryStore(tmp_path / "db.sqlite")
    for key, folder in [("a", "work/backend/a"), ("b", "work/backend/b"), ("c", "other")]:
        history.record(
            {"_id": key, "_ts": 1, "event_type": "SessionStart", "session_key": key, "test": True}
        )
        history.set_meta(key, name=f"Session {key}", group=folder)
    yield history
    history.close()


def test_folder_interactions_auth_scope_and_completed_history(store: HistoryStore) -> None:
    server = Server(history=store)
    a, b = enroll(store, "a"), enroll(store, "b")
    question = ask(store, a)
    call(store, b, "POST", f"/questions/{question['id']}/answer", {"text": "Contract ready"})
    owner = {"x-duckterm-token": server.token}
    path = "/folder-interactions?folder=work"
    assert dispatch(server, "GET", path, {})[0] == 401
    assert dispatch(server, "GET", path, a)[0] == 403
    for folder in ["work", "work/backend", "work/backend/a", "work/backend/b"]:
        status, page = dispatch(
            server, "GET", f"/folder-interactions?folder={quote(folder)}", owner
        )
        assert status == 200
        assert len(page["messages"]) == 1  # sender and recipient do not duplicate a row
        message = page["messages"][0]
        assert message["answer"] == "Contract ready"
        assert message["recipient_name"] == "Session b"
    assert dispatch(server, "GET", "/folder-interactions?folder=other", owner)[1]["messages"] == []
    assert dispatch(server, "GET", "/folder-interactions?folder=wor", owner)[0] == 404
    for cursor in ["bad", "0", "-1", "9" * 100, ""]:
        assert dispatch(server, "GET", path + "&before=" + cursor, owner)[0] == 400
    store.record(
        {"_id": "end", "_ts": 2, "event_type": "SessionEnd", "session_key": "b", "test": True}
    )
    assert dispatch(server, "GET", path, owner)[1]["messages"][0]["answer"] == "Contract ready"


def test_folder_history_pagination_and_literal_folder_names(store: HistoryStore) -> None:
    a, b = enroll(store, "a"), enroll(store, "b")
    for i in range(53):
        question = ask(store, a, f"page-{i}")
        call(store, b, "POST", f"/questions/{question['id']}/answer", {"text": str(i)})
        # Avoid the real-time per-sender request limiter in this pagination fixture.
        store._conn.execute("UPDATE session_questions SET created_at = created_at - 61000")
    store.set_meta("a", group="work/100%_done")
    store.set_meta("b", group="work/100%_done/child")
    first = store.session_api.folder_inbox("work/100%_done")
    second = store.session_api.folder_inbox("work/100%_done", before=first["next_cursor"])
    assert len(first["messages"]) == 50
    assert len(second["messages"]) == 3
    assert len({m["id"] for m in first["messages"] + second["messages"]}) == 53
    assert second["next_cursor"] is None
    assert store.session_api.folder_inbox("work/100")["messages"] == []


@pytest.mark.parametrize("folder", ["work", "work/backend/a"])
def test_empty_folder_history(store: HistoryStore, folder: str) -> None:
    assert store.session_api.folder_inbox(folder) == {"messages": [], "next_cursor": None}
