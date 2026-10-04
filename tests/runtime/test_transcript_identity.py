"""Transcript ownership and launch names survive polling and reopening."""

import json
import os
import time
from pathlib import Path

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import ClaudeCodeRuntime, project_slug
from duckterm.runtimes.codex import CodexRuntime
from duckterm.server import Server

A = "0192b256-a4a4-435c-b154-a9fe4be2c2a8"
B = "0192b256-a4a4-435c-b154-a9fe4be2c2a9"


def seed(store, cwd, key, runtime, sid=None, name=None):
    store.record(
        {
            "_id": key,
            "_ts": int(time.time() * 1000),
            "event_type": "SessionStart",
            "session_key": key,
            "session_id": sid,
            "runtime": runtime,
            "cwd": str(cwd),
            "source_app": cwd.name,
            "name": name,
            "test": True,
            "launched": True,
            "parent_session_key": "parent" if key == "child" else None,
        }
    )


def transcript(home, cwd, sid, runtime, text):
    if runtime == "claude-code":
        path = home / ".claude/projects" / project_slug(cwd) / f"{sid}.jsonl"
        obj = {"type": "user", "message": {"role": "user", "content": text}}
    else:
        path = home / ".codex/sessions/2026/09/30" / f"rollout-2026-09-30-{sid}.jsonl"
        obj = {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": text},
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj) + "\n")
    return path


@pytest.mark.parametrize("runtime", ["claude-code", "codex"])
def test_parent_child_stay_on_recorded_conversations_across_writes_and_reopen(
    tmp_path, monkeypatch, runtime
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    cwd = tmp_path / "repo"
    cwd.mkdir()
    db = tmp_path / "db.sqlite"
    store = HistoryStore(db)
    seed(store, cwd, "parent", runtime, A, "Parent")
    seed(store, cwd, "child", runtime, B, "Parent (fork)")
    store.close()
    store = HistoryStore(db)
    server = Server(history=store)
    try:
        for turn in range(3):
            for key, sid in [("parent", A), ("child", B)]:
                path = transcript(tmp_path, cwd, sid, runtime, f"{key}-{turn}")
                os.utime(path, (time.time() + turn, time.time() + turn))
                status, payload = dispatch(server, "GET", f"/sessions/{key}/messages", {})
                assert status == 200
                assert payload["messages"][0]["blocks"][0]["text"] == f"{key}-{turn}"
            # Newest file is the child; parent must remain bound to A.
            assert server._session_messages("parent")[0]["blocks"][0]["text"] == f"parent-{turn}"
            assert store.session("child")["name"] == "Parent (fork)"
    finally:
        store.purge_test_sessions()
        store.close()


@pytest.mark.parametrize("runtime", ["claude-code", "codex"])
@pytest.mark.parametrize("sid", [None, A])
def test_missing_identity_or_local_file_does_not_show_peer(tmp_path, monkeypatch, runtime, sid):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    transcript(tmp_path, tmp_path, B, runtime, "another conversation")
    store = HistoryStore(tmp_path / "db.sqlite")
    seed(store, tmp_path, "child", runtime, sid)
    server = Server(history=store)
    try:
        assert server._session_messages("child") == []
        status, payload = dispatch(server, "GET", "/sessions/child/messages", {})
        assert status == 200 and payload["messages"] == []
        assert payload["transcript"]["status"] == ("not_found" if sid else "identity_missing")
        rt = ClaudeCodeRuntime() if runtime == "claude-code" else CodexRuntime()
        assert rt.find_resumable_id(cwd=tmp_path, recorded=sid) is None
    finally:
        store.purge_test_sessions()
        store.close()


@pytest.mark.parametrize("sid", [None, A])
def test_claude_resume_refuses_missing_identity_even_without_peer_row(tmp_path, monkeypatch, sid):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    transcript(tmp_path, tmp_path, B, "claude-code", "peer")
    store = HistoryStore(tmp_path / "db.sqlite")
    seed(store, tmp_path, "child", "claude-code", sid)
    store.set_state("child", "stopped", now=int(time.time() * 1000))
    server = Server(history=store)

    async def forbidden(**kwargs):
        pytest.fail("unsafe resume launched")

    monkeypatch.setattr(server.orchestrator, "launch", forbidden)
    try:
        status, payload = dispatch(
            server, "POST", "/sessions/child/resume", {"x-duckterm-token": server.token}
        )
        assert status == 409 and payload["code"] == "ambiguous_resume_identity"
        row = store.session("child")
        assert server._restore_session_with_resume_id(row)["_no_resume"] is True
        assert server._carry_context_argv(row, "child", tmp_path, "claude") is None
    finally:
        store.purge_test_sessions()
        store.close()


def test_persisted_launch_name_recovers_old_rows_without_overwriting_rename(tmp_path):
    db = tmp_path / "db.sqlite"
    store = HistoryStore(db)
    for key in ["child", "renamed", "cleared", "unnamed"]:
        seed(
            store,
            tmp_path,
            key,
            "claude-code",
            name="Original (fork)" if key != "unnamed" else None,
        )
    assert store.session("child")["name"] == "Original (fork)"
    store.set_meta("renamed", name="Owner label")
    store.set_meta("cleared", name="")
    # Simulate a pre-fix database which discarded the event's explicit name.
    store._conn.execute("UPDATE sessions SET name=NULL WHERE session_key='child'")
    store._conn.commit()
    store.close()
    store = HistoryStore(db)
    try:
        assert store.session("child")["name"] == "Original (fork)"
        assert store.session("renamed")["name"] == "Owner label"
        assert store.session("cleared")["name"] == ""
        assert store.session("unnamed")["name"] is None
        # Resume/late hooks cannot reset a persisted owner name.
        store.record(
            {
                "_id": "late",
                "_ts": int(time.time() * 1000),
                "event_type": "SessionStart",
                "session_key": "renamed",
                "name": "Original (fork)",
                "source_app": "folder",
                "test": True,
            }
        )
        assert store.session("renamed")["name"] == "Owner label"
    finally:
        store.purge_test_sessions()
        store.close()


def test_fork_waits_for_child_hook_then_resume_uses_child_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    transcript(tmp_path, tmp_path, A, "claude-code", "parent history")
    store = HistoryStore(tmp_path / "db.sqlite")
    seed(store, tmp_path, "parent", "claude-code", A, "Named parent")
    server = Server(history=store)
    launches = []

    async def launch(**kwargs):
        launches.append(
            kwargs["runtime"].launch_command(
                cwd=tmp_path, session_key=kwargs["session_key"], initial_prompt=""
            )
        )
        server.bus.publish(
            {
                "event_type": "SessionStart",
                "session_key": kwargs["session_key"],
                "runtime": "claude-code",
                "cwd": str(tmp_path),
                "test": True,
                "launched": True,
                "name": kwargs.get("name"),
                "parent_session_key": kwargs.get("parent_session_key"),
            }
        )
        return kwargs["session_key"]

    monkeypatch.setattr(server.orchestrator, "launch", launch)
    try:
        headers = {"x-duckterm-token": server.token}
        status, fork = dispatch(
            server,
            "POST",
            "/sessions/parent/fork-conversation",
            headers,
            json.dumps({"in_terminal": False, "test": True}).encode(),
        )
        assert status == 200 and fork["carried_conversation"] is True
        key = fork["session_key"]
        assert launches == [["claude", "--resume", A, "--fork-session"]]
        assert store.session(key)["name"] == "Named parent (fork)"
        missing = json.loads(server._session_messages_response(key))
        assert missing["messages"] == []
        assert missing["transcript"]["status"] == "identity_missing"
        transcript(tmp_path, tmp_path, B, "claude-code", "child history")
        status, _ = dispatch(
            server,
            "POST",
            "/events",
            headers,
            json.dumps(
                {
                    "event_type": "SessionStart",
                    "session_key": key,
                    "session_id": B,
                    "runtime": "claude-code",
                    "cwd": str(tmp_path),
                    "test": True,
                }
            ).encode(),
        )
        assert status == 200 and store.session_id_for(key) == B
        assert server._session_messages(key)[0]["blocks"][0]["text"] == "child history"
        assert server._session_messages("parent")[0]["blocks"][0]["text"] == "parent history"
        store.set_state(key, "stopped", now=int(time.time() * 1000))
        status, resumed = dispatch(server, "POST", f"/sessions/{key}/resume", headers)
        assert status == 200 and resumed["carried_conversation"] is True
        assert launches[-1] == ["claude", "--resume", B]
        assert store.session(key)["name"] == "Named parent (fork)"
    finally:
        store.purge_test_sessions()
        store.close()
