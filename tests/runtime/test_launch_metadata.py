"""Launch routes preserve cleanup and folder metadata without starting an agent."""

import json
from pathlib import Path
from typing import Any

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.mark.parametrize("mode", ["default", "terminal", "headless", "hosted"])
@pytest.mark.parametrize("named", [False, True])
def test_launch_preserves_test_flag_and_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, named: bool
) -> None:
    monkeypatch.delenv("DUCKTERM_HOSTED", raising=False)
    if mode == "hosted":
        monkeypatch.setenv("DUCKTERM_HOSTED", "1")
    store = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=store)
    monkeypatch.setattr(server, "_collaboration_prompt", lambda *args: "")
    monkeypatch.setattr(server, "_session_env", lambda key: {})
    terminal_calls = []
    monkeypatch.setattr(
        "duckterm.server.open_in_terminal",
        lambda *args, **kwargs: terminal_calls.append(args) or True,
    )
    launches = []

    async def launch(**kwargs: Any) -> str:
        launches.append(kwargs)
        key = kwargs["session_key"]
        store.record(
            {
                "_id": "test-launch",
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "test": kwargs.get("test"),
            }
        )
        return key

    monkeypatch.setattr(server.orchestrator, "launch", launch)
    payload = {
        "command": "claude",
        "runtime": "claude-code",
        "cwd": str(tmp_path),
        "session_key": "test-launch-metadata",
        "test": True,
        "group": "Probe/Launch",
    }
    if mode in ("terminal", "headless"):
        payload["in_terminal"] = mode == "terminal"
    if named:
        payload.update(name="Launch probe", notes="Disposable fixture")
    try:
        status, result = dispatch(
            server,
            "POST",
            "/sessions/launch",
            {"x-duckterm-token": server.token},
            json.dumps(payload).encode(),
        )
        assert status == 200, result
        key = result["session_key"]
        row = store.session(key)
        assert row is not None
        assert row["test"] == 1
        assert row["grp"] == "Probe/Launch"
        if named:
            assert row["name"] == "Launch probe"
            assert row["notes"] == "Disposable fixture"
        assert bool(terminal_calls) == (mode in ("default", "terminal"))
        assert bool(launches) == (mode in ("headless", "hosted"))
        assert store.purge_test_sessions() == [key]
        assert store.session(key) is None
    finally:
        store.close()
