"""The worktree fork keeps the selected Codex identity in every edition."""

import json
from pathlib import Path

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

PARENT_ID = "0192b256-a4a4-435c-b154-a9fe4be2c2a8"
PEER_ID = "0192b256-a4a4-435c-b154-a9fe4be2c2a9"


@pytest.mark.parametrize("edition", ["lite", "code", "full"])
@pytest.mark.parametrize("carry,recorded", [(True, PARENT_ID), (False, PARENT_ID), (True, None)])
def test_codex_worktree_fork_preserves_parent_identity(
    tmp_path, monkeypatch, git_repo, edition, carry, recorded
):
    monkeypatch.setenv("DUCKTERM_EDITION", edition)
    home = tmp_path / "home"
    rollouts = home / ".codex/sessions/2026/11/07"
    rollouts.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for hour, sid in [(10, PARENT_ID), (11, PEER_ID)]:
        (rollouts / f"rollout-2026-11-07T{hour}-00-00-{sid}.jsonl").write_text(
            json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": str(git_repo)}})
            + "\n"
        )
    store = HistoryStore(tmp_path / "history.sqlite")
    try:
        for key, sid in [("parent", recorded), ("newer-peer", PEER_ID)]:
            store.record(
                {
                    "_id": key,
                    "_ts": 1,
                    "event_type": "SessionStart",
                    "session_key": key,
                    "session_id": sid,
                    "runtime": "codex",
                    "cwd": str(git_repo),
                    "repo_path": str(git_repo),
                    "branch": "main",
                    "test": True,
                }
            )
        before = store.session("parent")
        server = Server(history=store)
        launches = []

        async def launch(**kwargs):
            runtime = kwargs.pop("runtime")
            launches.append(
                {
                    **kwargs,
                    "runtime": runtime.name,
                    "argv": runtime.launch_command(
                        cwd=git_repo, session_key="test-child", initial_prompt=""
                    ),
                }
            )
            return "test-child"

        monkeypatch.setattr(server.orchestrator, "launch", launch)
        status, result = dispatch(
            server,
            "POST",
            "/sessions/parent/fork",
            {"x-duckterm-token": server.token},
            json.dumps(
                {
                    "branch": "isolated-fork",
                    "command": "codex",
                    "carry_context": carry,
                    "in_terminal": False,
                }
            ).encode(),
        )
        assert status == 200, result
        expected_carry = bool(carry and recorded)
        assert result["carried_context"] is expected_carry
        assert result["parent_session_key"] == "parent"
        assert result["opened_in_terminal"] is False
        assert len(launches) == 1
        launch = launches[0]
        assert launch["argv"] == (["codex", "fork", PARENT_ID] if expected_carry else ["codex"])
        assert launch["runtime"] == "codex"
        assert launch["repo_path"] == str(git_repo)
        assert launch["base"] == "main" and launch["branch"] == "isolated-fork"
        assert launch["parent_session_key"] == "parent" and launch["test"] is True
        assert store.session_id_for("parent") == recorded
        assert store.session_id_for("newer-peer") == PEER_ID
        assert store.session("parent") == before
    finally:
        store.close()
