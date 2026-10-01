"""Forks have independent session identity and retain test isolation."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("conversation", [False, True])
@pytest.mark.parametrize("parent_test,request_test", [(True, False), (False, True)])
def test_fork_identity_and_test_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: bool,
    conversation: bool,
    parent_test: bool,
    request_test: bool,
) -> None:
    store = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=store)
    store.record(
        {
            "_id": "parent-start",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "parent",
            "runtime": "claude-code",
            "cwd": str(tmp_path),
            "repo_path": str(tmp_path),
            "branch": "main",
            "test": True,
        }
    )
    # All persisted fixtures are test:true; simulate an ordinary parent only
    # in the route's read to exercise an explicit test request independently.
    parent = store.session("parent")
    assert parent is not None
    parent["test"] = parent_test
    original_session = store.session
    monkeypatch.setattr(
        store, "session", lambda key: parent if key == "parent" else original_session(key)
    )
    monkeypatch.setattr(server, "_resumable_session_id", lambda key, cwd: "12345678-recorded")
    monkeypatch.setattr("duckterm.server.open_in_terminal", lambda *args, **kwargs: True)
    monkeypatch.setattr(server, "_collaboration_prompt", lambda *args: "")
    monkeypatch.setattr(server, "_session_env", lambda key: {})
    monkeypatch.setattr(
        server.orchestrator.worktrees,
        "add",
        lambda repo, branch, **kwargs: SimpleNamespace(path=tmp_path, branch=branch),
    )
    launches: list[dict[str, Any]] = []

    async def launch(**kwargs: Any) -> str:
        launches.append(kwargs)
        key = str(kwargs.get("session_key") or f"test-child-{len(launches)}")
        store.record(
            {
                "_id": key,
                "_ts": 2,
                "event_type": "SessionStart",
                "session_key": key,
                "parent_session_key": kwargs["parent_session_key"],
                "test": kwargs.get("test", False),
            }
        )
        return key

    monkeypatch.setattr(server.orchestrator, "launch", launch)
    route = "fork-conversation" if conversation else "fork"
    children: list[str] = []
    try:
        for n in range(2):
            status, result = dispatch(
                server,
                "POST",
                f"/sessions/parent/{route}",
                {"x-duckterm-token": server.token},
                json.dumps(
                    {"in_terminal": terminal, "test": request_test, "branch": f"child-{n}"}
                ).encode(),
            )
            assert status == 200, result
            key = result["session_key"]
            children.append(key)
            row = original_session(key)
            assert row is not None
            assert row["test"] == 1
            assert row["parent_session_key"] == "parent"
            if conversation:
                assert "claude --resume 12345678-recorded --fork-session" in result["command"]
        assert children[0] != children[1]
        assert original_session(children[0])["session_key"] == children[0]  # type: ignore[index]
        if not terminal:
            assert all(call["test"] is True for call in launches)
    finally:
        store.purge_test_sessions()
        store.close()
