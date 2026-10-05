"""Timeline pagination preserves timestamp ties and never invents missing history."""

import json

import pytest
from tests.runtime.test_session_api import dispatch, enroll

from duckterm.persistence.digests import DigestStore
from duckterm.persistence.history import HistoryStore
from duckterm.persistence.timeline import page
from duckterm.server import Server


@pytest.fixture
def stores(tmp_path):
    history = HistoryStore(tmp_path / "history.sqlite")
    digest = DigestStore(tmp_path / "digest.sqlite")
    history.record(
        {"_id": "start", "_ts": 1, "event_type": "SessionStart", "session_key": "a", "test": True}
    )
    yield history, digest
    digest.close()
    history.close()


def read(stores, **kwargs):
    h, d = stores
    return page(h._conn, d._conn, h.session("a"), [], now=1000, **kwargs)


def test_tied_pagination_excludes_new_and_backdated_inserts(stores):
    h, _ = stores
    for i in range(9):
        h.record(
            {
                "_id": str(i),
                "_ts": 10,
                "event_type": "UserPromptSubmit",
                "session_key": "a",
                "prompt": f"Prompt {i}",
                "test": True,
            }
        )
    first = read(stores, limit=3, kinds="prompt")
    for stamp in [5, 10, 20]:
        h.record(
            {
                "_id": f"new{stamp}",
                "_ts": stamp,
                "event_type": "UserPromptSubmit",
                "session_key": "a",
                "prompt": "New",
                "test": True,
            }
        )
    entries = first["entries"]
    cursor = first["next_cursor"]
    while cursor:
        result = read(stores, limit=3, kinds="prompt", before=cursor)
        assert result["summary"]["total"] == 9
        entries += result["entries"]
        cursor = result["next_cursor"]
    assert [e["id"] for e in entries] == [f"prompt:{i}" for i in range(8, -1, -1)]
    assert read(stores, kinds="prompt")["summary"]["total"] == 12


def test_digest_first_seen_and_completion_are_distinct(stores):
    _, d = stores
    d.merge(
        "a",
        [
            {"bucket": "next_actions", "text": "Ship fix"},
            {"bucket": "deliverables", "text": "Patch"},
        ],
        [],
        10,
    )
    item = next(x for x in d.items("a") if x["bucket"] == "next_actions")
    d.merge("a", [], [item["id"]], 30)
    result = read(stores)
    assert {(e["kind"], e["ts"]) for e in result["entries"]} == {
        ("next_action", 10),
        ("delivered", 10),
        ("completed", 30),
    }
    assert read(stores, kinds="completed")["summary"]["counts"] == {"completed": 1}


def test_empty_sources_no_synthetic_restart_or_ledger(stores):
    assert read(stores)["entries"] == []
    assert read(stores)["next_cursor"] is None
    assert read(stores)["summary"]["total"] == 0


def test_checkpoint_artifact_tombstone_decision_and_wait(stores):
    h, d = stores
    h._conn.execute(
        "INSERT INTO checkpoints(id,session_key,label,summary,created_at) "
        "VALUES('cp','a','Before switch','saved',20)"
    )
    h._conn.execute(
        "INSERT INTO artifact_metadata(id,session_key,kind,kind_source,removed_at,snapshot_json) "
        "VALUES('file','a','report','declared',40,?)",
        (json.dumps({"title": "Removed report", "created_at": 15}),),
    )
    h._conn.execute(
        "INSERT INTO session_questions(id,sender,recipient,sender_name,root,question,status,answer,"
        "created_at,expires_at,answered_at,idempotency_key,content_hash) "
        "VALUES('q','owner','a','Owner','root','Ship?','answered','Ready',5,0,25,'q','hash')"
    )
    h._conn.commit()
    notes = [
        {
            "id": "n",
            "session_key": "a",
            "created_at": 8,
            "closed_at": 18,
            "status": "answered",
            "question": "Which model?",
        },
        {"id": "other", "session_key": "b", "created_at": 8},
        {"id": "offer", "session_key": "a", "created_at": 8, "urgency": "offer"},
    ]
    result = page(h._conn, d._conn, h.session("a"), notes, now=1000)
    assert [e["kind"] for e in result["entries"]] == [
        "decision",
        "checkpoint",
        "artifact",
        "needs-you",
    ]
    assert result["entries"][2]["detail"]["removed"] is True
    assert result["entries"][3]["detail"]["duration_ms"] == 10
    assert sum(result["summary"]["counts"].values()) == len(result["entries"])


def test_endpoint_validation_auth_and_read_only(stores, monkeypatch):
    h, d = stores
    server = Server(history=h)
    server.digests.close()
    server.digests = d
    headers = {"x-duckterm-token": server.token}
    assert dispatch(server, "GET", "/sessions/a/timeline", {})[0] == 401
    h.set_meta("a", group="work")
    peer = enroll(h, "a")
    assert dispatch(server, "GET", "/sessions/a/timeline", peer)[0] == 403
    assert dispatch(server, "GET", "/sessions/missing/timeline", headers)[0] == 404
    for query in [
        "limit=0",
        "limit=101",
        "limit=no",
        "before=oops",
        "kinds=madeup",
        "limit=1&limit=2",
    ]:
        assert dispatch(server, "GET", "/sessions/a/timeline?" + query, headers)[0] == 400
    before = (h._conn.total_changes, d._conn.total_changes)
    status, result = dispatch(server, "GET", "/sessions/a/timeline?limit=10", headers)
    assert status == 200 and result["entries"] == []
    assert (h._conn.total_changes, d._conn.total_changes) == before


def test_cursor_bound_to_session_and_filters(stores):
    _, d = stores
    d.merge(
        "a",
        [{"bucket": "learnings", "text": "One"}, {"bucket": "learnings", "text": "Two"}],
        [],
        10,
    )
    cursor = read(stores, limit=1, kinds="learned")["next_cursor"]
    with pytest.raises(ValueError, match="cursor"):
        read(stores, before=cursor, kinds="completed")


def test_absent_tables_and_open_wait_duration(stores):
    import sqlite3

    h, _ = stores
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        result = page(
            conn,
            conn,
            h.session("a"),
            [{"id": "open", "session_key": "a", "created_at": 5, "status": "open"}],
            now=1000,
        )
        assert len(result["entries"]) == 1
        assert result["entries"][0]["detail"]["duration_ms"] is None
        assert result["summary"]["counts"]["artifact"] == 0
    finally:
        conn.close()
