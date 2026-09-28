"""A cwd is shared context, never a Codex conversation identifier."""

import json
import time
from pathlib import Path

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.codex import CodexRuntime
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server

A = "0192b256-a4a4-435c-b154-a9fe4be2c2a8"
B = "0192b256-a4a4-435c-b154-a9fe4be2c2a9"


@pytest.fixture
def conversations(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / ".codex/sessions/2026/09/28"
    root.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    for hour, sid in [(10, A), (11, B)]:
        (root / f"rollout-2026-09-28T{hour}-00-00-{sid}.jsonl").write_text(
            json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": str(repo)}}) + "\n"
        )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("DUCKTERM_HOME", str(home / ".duckterm"))
    return repo


def seed(store, repo, key, sid=None):
    now = int(time.time() * 1000)
    store.record(
        {
            "_id": key,
            "_ts": now,
            "event_type": "SessionStart",
            "session_key": key,
            "runtime": "codex",
            "cwd": str(repo),
            "launched": True,
            "test": True,
        }
    )
    if sid:
        # Native launch hook is durably associated with DuckTerm's own row key.
        store.record(
            {
                "_id": key + "-hook",
                "_ts": now + 1,
                "event_type": "SessionStart",
                "session_key": key,
                "session_id": sid,
                "runtime": "codex",
                "test": True,
            }
        )
    store.set_state(key, "stopped", now=now + 2)


def test_two_shared_directory_conversations_resume_their_recorded_ids_after_restart(
    tmp_path,
    conversations,
    monkeypatch,
):
    db = tmp_path / "db.sqlite"
    store = HistoryStore(db)
    seed(store, conversations, "a", A)
    seed(store, conversations, "b", B)
    store.close()
    store = HistoryStore(db)
    try:
        server = Server(history=store)
        assert store.session_id_for("a") == A
        assert CodexRuntime().find_resumable_id(cwd=conversations, recorded=A) == A
        launches = {}

        async def launch(*, runtime, cwd, session_key, **kwargs):
            launches[session_key] = runtime.launch_command(
                cwd=Path(cwd), session_key=session_key, initial_prompt=""
            )
            return session_key

        monkeypatch.setattr(server.orchestrator, "launch", launch)
        for key, sid in [("a", A), ("b", B)]:
            status, body = dispatch(
                server, "POST", f"/sessions/{key}/resume", {"x-duckterm-token": server.token}
            )
            assert status == 200 and body["carried_conversation"] is True, body
            assert launches[key] == ["codex", "resume", sid]
    finally:
        store.close()


@pytest.mark.parametrize("sid", [None, "ffffffff-ffff-ffff-ffff-ffffffffffff", "*"])
def test_unknown_or_stale_shared_identity_is_refused_without_launch(
    tmp_path,
    conversations,
    monkeypatch,
    sid,
):
    store = HistoryStore(tmp_path / "db.sqlite")
    try:
        seed(store, conversations, "unknown", sid)
        seed(store, conversations, "peer", B)
        server = Server(history=store)

        async def forbidden(**kwargs):
            pytest.fail("ambiguous Resume launched a process")

        monkeypatch.setattr(server.orchestrator, "launch", forbidden)
        status, body = dispatch(
            server, "POST", "/sessions/unknown/resume", {"x-duckterm-token": server.token}
        )
        assert status == 409
        assert body["code"] == "ambiguous_resume_identity"
        assert store.session("unknown")["state"] == "stopped"
        # Snapshot restore and fork must not reintroduce the newest-cwd guess.
        row = store.session("unknown")
        assert server._restore_session_with_resume_id(row)["_no_resume"] is True
        assert server._carry_context_argv(row, "unknown", conversations, "codex") is None
    finally:
        store.close()


def test_exact_resume_capability_defaults_false_and_codex_never_guesses(conversations):
    assert not GenericRuntime("true").can_resume_unambiguously(cwd=conversations, recorded=A)
    codex = CodexRuntime()
    assert not codex.can_resume_unambiguously(cwd=conversations, recorded=None)
    assert codex.can_resume_unambiguously(cwd=conversations, recorded=A)
    assert codex.find_resumable_id(cwd=conversations, recorded=None) is None
