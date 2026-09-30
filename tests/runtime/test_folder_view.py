"""Folder views use exact derived scope, private history, and recoverable moves."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.core import oracle
from duckterm.helpers import paths
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def app(tmp_path):
    history = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=history)
    for key, folder in [("one", "a"), ("two", "a/child"), ("outside", "ab"), ("literal", "a%_")]:
        history.record(
            {"_id": key, "_ts": 1, "event_type": "SessionStart", "session_key": key, "test": True}
        )
        history.set_meta(key, name=f"Session {key}", group=folder)
        history.artifacts.register(
            key,
            {
                "title": key,
                "source_path": f"/tmp/{key}.md",
                "content_base64": base64.b64encode(b"report").decode(),
            },
        )
    yield server
    history.purge_test_sessions()
    history.close()


def req(app, method, path, data=None, headers=None):
    return dispatch(
        app,
        method,
        path,
        {"x-duckterm-token": app.token} if headers is None else headers,
        json.dumps(data).encode() if data is not None else b"",
    )


def test_artifact_scope_auth_and_session_lifecycle(app):
    assert req(app, "GET", "/folders/a/artifacts", headers={})[0] == 401
    assert req(app, "GET", "/folders/a/chat", headers={})[0] == 401
    assert req(app, "GET", "/folders/a/chat", headers={"authorization": "Bearer no"})[0] == 403
    app.history.set_state("two", "archived")
    status, body = req(app, "GET", "/folders/a/artifacts")
    assert status == 200
    assert {r["session_key"] for r in body["artifacts"]} == {"one", "two"}
    assert (
        next(r for r in body["artifacts"] if r["session_key"] == "two")["session_state"]
        == "archived"
    )
    assert body["artifacts"][0]["session_name"].startswith("Session ")
    assert [r["title"] for r in req(app, "GET", "/folders/a%25_/artifacts")[1]["artifacts"]] == [
        "literal"
    ]
    app.history.set_meta("one", group="ab")
    assert [r["title"] for r in req(app, "GET", "/folders/a/artifacts")[1]["artifacts"]] == ["two"]
    app.history.purge_test_sessions()
    app.history.create_folder("a")
    assert req(app, "GET", "/folders/a/artifacts")[1]["artifacts"] == []


def test_folder_ask_isolated_history_and_empty_scope(app, monkeypatch):
    prompts = []

    def summarize(prompt):
        prompts.append(prompt)
        return SimpleNamespace(text="Folder-specific answer", backend="test")

    monkeypatch.setattr("duckterm.server.summarize", summarize)
    oracle.append_chat(paths.home() / "oracle-chat.json", "GLOBAL_PRIVATE", "GLOBAL_REPLY", 1)
    other_id, _ = app.history.folder_chats.snapshot("ab")
    app.history.folder_chats.append("ab", other_id, "OTHER_PRIVATE", "OTHER_REPLY", 1)
    status, body = req(app, "POST", "/fleet/ask", {"folder": "a", "question": "What is happening?"})
    assert status == 200 and set(body["sessions"]) == {"one", "two"}
    assert "Session one" in prompts[0] and "Session two" in prompts[0]
    assert not any(
        secret in prompts[0]
        for secret in ["GLOBAL_PRIVATE", "OTHER_PRIVATE", "Session outside", "Session literal"]
    )
    assert req(app, "GET", "/folders/a/chat")[1]["messages"] == [body["exchange"]]
    assert req(app, "POST", "/fleet/ask", {"folder": "a", "question": "Follow up"})[0] == 200
    assert "Folder-specific answer" in prompts[1]
    app.history.create_folder("empty")
    assert (
        req(app, "POST", "/fleet/ask", {"folder": "empty", "question": "Anything?"})[1]["answer"]
        == "No sessions are running in empty."
    )
    assert len(prompts) == 2
    for bad in ["", None, 2, []]:
        assert req(app, "POST", "/fleet/ask", {"folder": bad, "question": "x"})[0] == 400
    assert req(app, "POST", "/fleet/ask", {"folder": "missing", "question": "x"})[0] == 404
    assert req(app, "POST", "/fleet/ask", {"folder": "a", "question": "x" * 8001})[0] == 400
    assert req(app, "POST", "/fleet/ask", ["bad"])[0] == 400


def test_subtree_rename_delete_and_late_answer(app, monkeypatch):
    for path in ["a", "a/child", "ab"]:
        identity, _ = app.history.folder_chats.snapshot(path)
        app.history.folder_chats.append(path, identity, path, "answer", 1)
    assert req(app, "PATCH", "/folders/a", {"name": "new"})[0] == 200
    assert req(app, "GET", "/folders/a/chat")[0] == 404
    assert req(app, "GET", "/folders/new%2Fchild/chat")[1]["messages"][0]["q"] == "a/child"
    assert req(app, "GET", "/folders/ab/chat")[1]["messages"][0]["q"] == "ab"
    assert len(req(app, "GET", "/folders/new/artifacts")[1]["artifacts"]) == 2
    assert req(app, "PATCH", "/folders/new", {"name": "ab"})[0] == 400

    def summarize(_):
        # A rename while inference is in a worker thread invalidates its result.
        app.history.folder_chats.change(
            "new", "later", lambda: app.history.move_folder("new", "later")
        )
        return SimpleNamespace(text="Stale answer", backend="test")

    monkeypatch.setattr("duckterm.server.summarize", summarize)
    assert req(app, "POST", "/fleet/ask", {"folder": "new", "question": "x"})[0] == 409
    assert "Stale answer" not in json.dumps(req(app, "GET", "/folders/later/chat")[1])
    assert req(app, "DELETE", "/folders/later")[0] == 200
    app.history.create_folder("later")
    assert req(app, "GET", "/folders/later/chat")[1]["messages"] == []


def test_journal_recovers_after_db_commit_and_preserves_before_commit(app):
    chats = app.history.folder_chats
    identity, _ = chats.snapshot("a/child")
    chats.append("a/child", identity, "keep", "answer", 1)
    for committed in [False, True]:
        data = json.loads(chats.path.read_text())
        data["pending"] = {"old": "a", "new": "moved"}
        chats._save(data)
        if committed:
            app.history.move_folder("a", "moved")
        target = "moved/child" if committed else "a/child"
        assert chats.snapshot(target)[1][0]["q"] == "keep"
        assert "pending" not in json.loads(chats.path.read_text())


def test_parallel_appends_are_not_lost_and_history_is_capped(app):
    chats = app.history.folder_chats
    identity, _ = chats.snapshot("a")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: chats.append("a", identity, str(i), "answer", i), range(210)))
    messages = chats.snapshot("a")[1]
    assert len(messages) == oracle.CHAT_LIMIT
    assert len({m["q"] for m in messages}) == oracle.CHAT_LIMIT
    assert chats.path.stat().st_mode & 0o777 == 0o600


def test_corrupt_history_degrades_to_empty_and_keeps_recovery_copy(app):
    chats = app.history.folder_chats
    chats.path.write_text("{broken")
    assert chats.snapshot("a")[1] == []
    assert next(chats.path.parent.glob("folder-chats-corrupt-*.json")).read_text() == "{broken"
    for folder in ["../a", "a/../ab", "a//child"]:
        app.history.create_folder(folder)
        assert req(app, "POST", "/fleet/ask", {"folder": folder, "question": "x"})[0] == 400


def test_startup_recovers_committed_delete_before_name_is_recreated(app):
    chats = app.history.folder_chats
    identity, _ = chats.snapshot("a")
    chats.append("a", identity, "old private question", "old answer", 1)
    data = json.loads(chats.path.read_text())
    data["pending"] = {"old": "a", "new": None}
    chats._save(data)
    app.history.delete_folder("a")
    # Restart after the DB commit but before JSON completion.
    restarted = Server(history=app.history)
    restarted.history.create_folder("a")
    assert restarted.history.folder_chats.snapshot("a")[1] == []
