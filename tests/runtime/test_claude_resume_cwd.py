"""Resume and recovery share exact-identity discovery after directory drift."""

import asyncio

import pytest
from tests.runtime.test_conversation_recovery import transcript

from duckterm.conversation_files import candidates, excerpts
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.server import Server


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    original = tmp_path / "project"
    current = original / "child"
    current.mkdir(parents=True)
    path = transcript(original, "recorded")
    history = HistoryStore(tmp_path / "history.sqlite")
    history.record(
        {
            "_id": "start",
            "_ts": 1,
            "session_key": "named-card",
            "session_id": "recorded",
            "event_type": "SessionStart",
            "runtime": "claude-code",
            "cwd": str(current),
            "launched": True,
            "test": True,
        }
    )
    history.set_state("named-card", "stopped", now=2)
    history.set_meta("named-card", name="My named session")
    server = Server(history=history)
    calls = []

    async def launch(**kwargs):
        calls.append(kwargs)
        return kwargs["session_key"]

    monkeypatch.setattr(server.orchestrator, "launch", launch)
    yield server, current, path, calls
    server.digests.close()
    history.purge_test_sessions()
    history.close()


def test_resume_keeps_exact_identity_current_directory_and_card(rig):
    server, current, _, calls = rig

    async def run():
        recovery = await server.conversation_recovery.identity("named-card")
        assert recovery["canResume"] and recovery["transcript"] == "present"
        status, result = await server._resume_session("named-card", exact=True)
        assert status == 200 and result["carried_conversation"]
        assert result["command"] == ["claude", "--resume", "recorded"]

    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0]["cwd"] == str(current)
    assert calls[0]["session_key"] == "named-card" and calls[0]["test"]
    assert server.history.session("named-card")["name"] == "My named session"
    assert server.history.session_id_for("named-card") == "recorded"


def test_known_identity_lookup_does_not_expand_adoption_candidates(rig):
    _, current, path, _ = rig
    assert candidates("claude-code", str(current)) == ([], False)
    assert excerpts(path, "claude-code", str(current), False) is None
    assert excerpts(path, "claude-code", str(current), False, expected_native_id="recorded")
    assert excerpts(path, "claude-code", str(current), False, expected_native_id="peer") is None


def test_ambiguous_relocated_identity_never_launches(rig):
    server, _, path, calls = rig
    duplicate = path.parent.parent / "other" / path.name
    duplicate.parent.mkdir()
    duplicate.write_bytes(path.read_bytes())

    async def run():
        recovery = await server.conversation_recovery.identity("named-card")
        assert not recovery["canResume"]
        status, result = await server._resume_session("named-card")
        assert status == 409 and result["code"] == "ambiguous_resume_identity"

    asyncio.run(run())
    assert not calls


def test_identity_lost_between_check_and_command_never_launches_fresh(rig, monkeypatch):
    server, _, _, calls = rig
    monkeypatch.setattr(ClaudeCodeRuntime, "find_resumable_id", lambda *a, **kw: None)
    status, result = asyncio.run(server._resume_session("named-card"))
    assert status == 409 and result["code"] == "ambiguous_resume_identity"
    assert not calls
