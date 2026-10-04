"""Outstanding work stays discoverable behind pages of answered history."""

from collections.abc import Iterator
from pathlib import Path

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


@pytest.mark.parametrize("path", ["/sessions/b/inbox", "/folder-interactions?folder=work"])
def test_filter_before_pagination_and_scope(store: HistoryStore, path: str) -> None:
    a, b = enroll(store, "a"), enroll(store, "b")
    ids = []
    for i in range(107):
        question = ask(store, a, f"history-{i}")
        ids.append(question["id"])
        call(store, b, "POST", f"/questions/{question['id']}/answer", {"text": "Done"})
        store._conn.execute("UPDATE session_questions SET created_at = created_at - 61000")
    # The 55 oldest requests are outstanding, behind 52 newer answered records.
    store._conn.executemany(
        "UPDATE session_questions SET status = 'accepted', answer = NULL, answered_at = NULL "
        "WHERE id = ?",
        [(key,) for key in ids[:55]],
    )
    priority = store.session_api.owner_message(
        "b", "Please acknowledge", priority=True, request_key="priority-test"
    )
    store._conn.execute("UPDATE session_questions SET status = 'read' WHERE id = ?", (priority,))
    notice = store.session_api.owner_message("b", "For information only")
    server = Server(history=store)
    owner = {"x-duckterm-token": server.token}
    sep = "&" if "?" in path else "?"
    status, first = dispatch(server, "GET", path + sep + "view=pending", owner)
    assert status == 200
    assert first["counts"] == {"all": 109, "pending": 56, "answered": 52}
    assert len(first["messages"]) == 50
    assert first["messages"][0]["id"] == priority
    second = dispatch(
        server, "GET", path + sep + f"view=pending&before={first['next_cursor']}", owner
    )[1]
    assert len(second["messages"]) == 6
    assert second["next_cursor"] is None
    assert second["counts"] == first["counts"]  # totals ignore the page cursor
    assert {m["id"] for m in first["messages"] + second["messages"]} == set(ids[:55]) | {priority}
    assert notice not in {m["id"] for m in first["messages"]}
    assert all(m["requires_reply"] for m in first["messages"])
    history = dispatch(server, "GET", path + sep + "view=answered", owner)[1]
    assert len(history["messages"]) == 50
    assert all(m["status"] == "answered" for m in history["messages"])
    assert dispatch(server, "GET", path + sep + "view=bogus", owner)[0] == 400
    assert dispatch(server, "GET", path + sep + "view=pending&before=0", owner)[0] == 400
    assert dispatch(server, "GET", path + sep + "view=pending", {})[0] == 401
    assert dispatch(server, "GET", path + sep + "view=pending", b)[0] == 403
    assert dispatch(server, "GET", "/folder-interactions?folder=other&view=pending", owner)[1][
        "counts"
    ] == {"all": 0, "pending": 0, "answered": 0}
    assert dispatch(server, "GET", "/sessions/a/inbox?view=pending", owner)[1]["messages"] == []
    # An owner's inspection never records the session as having read anything.
    assert (
        store._conn.execute(
            "SELECT COUNT(*) FROM session_inbox_delivery WHERE last_read_at > 0"
        ).fetchone()[0]
        == 0
    )
    # Existing CLI pagination remains a history view, without the owner-only counts.
    cli = call(store, b, "GET", "/inbox")[1]
    assert "counts" not in cli
    assert len(cli["messages"]) == 50
    assert any(m["status"] == "answered" for m in cli["messages"])
