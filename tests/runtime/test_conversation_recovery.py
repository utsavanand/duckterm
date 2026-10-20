import asyncio
import json
import uuid
from pathlib import Path

import pytest
from tests.runtime.test_session_api import Writer, dispatch

from duckterm.conversation_files import candidates
from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import project_slug
from duckterm.server import Server


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    store = HistoryStore(tmp_path / "history.sqlite")
    yield store
    store.purge_test_sessions()
    store.close()


@pytest.fixture
def recovery(history, tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    history.record(
        {
            "_id": uuid.uuid4().hex,
            "_ts": 1,
            "session_key": "orphan",
            "event_type": "SessionStart",
            "runtime": "claude-code",
            "cwd": str(cwd),
            "launched": True,
            "test": True,
        }
    )
    history.record(
        {
            "_id": uuid.uuid4().hex,
            "_ts": 2,
            "session_key": "orphan",
            "event_type": "Notification",
            "lifecycle": "stopped",
            "test": True,
        }
    )
    server = Server(history=history)
    yield server.conversation_recovery, cwd
    server.digests.close()


def transcript(cwd, native="chosen", runtime="claude-code"):
    if runtime == "claude-code":
        path = Path.home() / ".claude/projects" / project_slug(cwd) / f"{native}.jsonl"
        rows = [
            {"cwd": str(cwd), "sessionId": native, "message": {"role": "user", "content": text}}
            for text in ["First request", "Last request"]
        ]
    else:
        path = Path.home() / ".codex/sessions/2026/10/06" / f"rollout-2026-{native}.jsonl"
        rows = [{"type": "session_meta", "payload": {"id": native, "cwd": str(cwd)}}] + [
            {
                "type": "response_item",
                "payload": {
                    "role": "user",
                    "type": "message",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
            for text in ["First request", "Last request"]
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def test_recovery_requires_owner_and_refuses_cross_origin(recovery):
    service, _ = recovery
    server = service.server
    for operation in ["recovery", "candidates"]:
        path = "/sessions/orphan/conversation-" + operation
        assert dispatch(server, "GET", path, {})[0] == 401
        assert dispatch(server, "GET", path, {"authorization": "Bearer agent-token"})[0] == 403
        writer = Writer()

        async def cross_origin(path=path, writer=writer):
            await server._dispatch(
                "GET",
                path,
                asyncio.StreamReader(),
                writer,
                {"x-duckterm-token": server.token, "origin": "https://evil.invalid"},
                b"",
            )

        asyncio.run(cross_origin())
        assert writer.data.split()[1] == b"403"
        assert dispatch(server, "GET", path, {"x-duckterm-token": server.token})[0] == 200


def test_attach_is_explicit_durable_and_does_not_resume(recovery):
    service, cwd = recovery
    transcript(cwd)

    async def run():
        listing = await service.list_candidates("orphan")
        assert service.server.history.native_identity("orphan") == {
            "native_id": None,
            "source": "none",
            "status": "missing",
        }
        candidate = listing["candidates"][0]
        assert candidate["firstPrompt"] == "First request"
        assert candidate["lastPrompt"] == "Last request"
        assert "path" not in candidate and "native_id" not in candidate
        result = await service.attach(
            "orphan", {"revision": listing["revision"], "handle": candidate["handle"]}
        )
        assert result["source"] == "adopted" and result["transcript"] == "present"
        assert service.server.history.session_id_for("orphan") == "chosen"
        assert service.server.history.session("orphan")["state"] == "stopped"
        assert service.server.orchestrator.get("orphan") is None
        assert not result["canAdopt"]
        with pytest.raises(APIError):
            await service.attach(
                "orphan", {"revision": listing["revision"], "handle": candidate["handle"]}
            )

    asyncio.run(run())


@pytest.mark.parametrize("change", ["file", "state", "duplicate", "foreign-session", "path-input"])
def test_stale_or_ambiguous_attach_never_changes_identity(recovery, change):
    service, cwd = recovery
    path = transcript(cwd)

    async def run():
        listing = await service.list_candidates("orphan")
        body = {"revision": listing["revision"], "handle": listing["candidates"][0]["handle"]}
        key = "orphan"
        if change == "file":
            path.write_text(path.read_text() + "{}\n")
        elif change == "state":
            service.server.history.record(
                {
                    "_id": uuid.uuid4().hex,
                    "_ts": 3,
                    "event_type": "UserPromptSubmit",
                    "session_key": key,
                    "test": True,
                }
            )
        elif change == "duplicate":
            service.server.history.record(
                {
                    "_id": uuid.uuid4().hex,
                    "_ts": 3,
                    "event_type": "SessionStart",
                    "session_key": "peer",
                    "session_id": "chosen",
                    "runtime": "claude-code",
                    "cwd": str(cwd),
                    "test": True,
                }
            )
        elif change == "foreign-session":
            key = "peer"
        else:
            body["path"] = str(path)
        with pytest.raises(APIError):
            await service.attach(key, body)
        assert service.server.history.native_identity("orphan") == {
            "native_id": None,
            "source": "none",
            "status": "missing",
        }

    asyncio.run(run())


def test_hook_install_does_not_claim_recovered_identity(recovery):
    service, _ = recovery

    async def run():
        before = await service.identity("orphan")
        assert before["hooks"]["status"] == "missing"
        after = await service.install("orphan", {})
        assert after["hooks"]["status"] == "configured"
        assert after["status"] == "missing" and not after["canResume"]
        assert service.server.history.native_identity("orphan") == {
            "native_id": None,
            "source": "none",
            "status": "missing",
        }

    asyncio.run(run())


def test_scanning_refuses_symlink_and_wrong_project_and_supports_codex(recovery):
    _, cwd = recovery
    good = transcript(cwd)
    wrong = cwd.with_name("other")
    wrong.mkdir()
    bad = good.with_name("bad.jsonl")
    bad.write_text(json.dumps({"cwd": str(wrong), "sessionId": "bad"}) + "\n")
    good.with_name("linked.jsonl").symlink_to(good)
    result, _ = candidates("claude-code", str(cwd))
    assert [r["native_id"] for r in result] == ["chosen"]
    transcript(cwd, "codex-id", "codex")
    result, _ = candidates("codex", str(cwd))
    assert [r["native_id"] for r in result] == ["codex-id"]


def test_missing_directory_never_scans_and_reads_never_write_settings(recovery, monkeypatch):
    service, _ = recovery
    config = Path.home() / ".claude/settings.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"unrelated":"keep me"}')
    conn = service.server.history._conn
    conn.execute("UPDATE sessions SET cwd=NULL, worktree_path=NULL WHERE session_key='orphan'")
    conn.commit()
    monkeypatch.setattr(
        "duckterm.conversation_files.candidates",
        lambda *a: pytest.fail("scanner called without a project"),
    )
    before = conn.total_changes

    async def run():
        result = await service.identity("orphan")
        assert not result["canAdopt"] and "directory" in result["reason"]
        with pytest.raises(APIError):
            await service.list_candidates("orphan")

    asyncio.run(run())
    assert config.read_text() == '{"unrelated":"keep me"}'
    assert conn.total_changes == before


def test_whole_file_fingerprint_detects_middle_rewrite_and_caps_large_files(recovery):
    from duckterm.conversation_files import MAX_FILE_BYTES, WINDOW, excerpts

    _, cwd = recovery
    path = transcript(cwd)
    path.write_bytes(path.read_bytes() + (b" " * (WINDOW * 3)) + b"\n")
    first = excerpts(path, "claude-code", str(cwd))
    with path.open("r+b") as stream:
        stream.seek(WINDOW + 100)
        stream.write(b"X")
    second = excerpts(path, "claude-code", str(cwd))
    assert first["fingerprint"][-1] != second["fingerprint"][-1]
    with path.open("r+b") as stream:
        stream.truncate(MAX_FILE_BYTES + 1)
    large = excerpts(path, "claude-code", str(cwd))
    assert not large["available"] and "32 MiB" in large["reason"]


def test_expired_handle_and_changed_state_during_scan_are_rejected(recovery, monkeypatch):
    service, cwd = recovery
    transcript(cwd)

    async def run():
        listing = await service.list_candidates("orphan")
        service.lists[listing["revision"]]["expires"] = 0
        with pytest.raises(APIError):
            await service.attach(
                "orphan",
                {"revision": listing["revision"], "handle": listing["candidates"][0]["handle"]},
            )
        original = asyncio.to_thread

        async def interleave(function, *args, **kwargs):
            value = await original(function, *args, **kwargs)
            if function is candidates:
                service.server.history.record(
                    {
                        "_id": uuid.uuid4().hex,
                        "_ts": 99,
                        "session_key": "orphan",
                        "event_type": "UserPromptSubmit",
                        "test": True,
                    }
                )
            return value

        monkeypatch.setattr(asyncio, "to_thread", interleave)
        with pytest.raises(APIError):
            await service.list_candidates("orphan")
        assert service.server.history.native_identity("orphan") == {
            "native_id": None,
            "source": "none",
            "status": "missing",
        }

    asyncio.run(run())


def test_two_cards_cannot_adopt_one_conversation(recovery):
    service, cwd = recovery
    transcript(cwd)
    history = service.server.history
    history.record(
        {
            "_id": uuid.uuid4().hex,
            "_ts": 1,
            "session_key": "second",
            "event_type": "SessionStart",
            "runtime": "claude-code",
            "cwd": str(cwd),
            "launched": True,
            "test": True,
        }
    )
    history.record(
        {
            "_id": uuid.uuid4().hex,
            "_ts": 2,
            "session_key": "second",
            "event_type": "Notification",
            "lifecycle": "stopped",
            "test": True,
        }
    )

    async def run():
        one = await service.list_candidates("orphan")
        two = await service.list_candidates("second")
        results = await asyncio.gather(
            *[
                service.attach(
                    key, {"revision": value["revision"], "handle": value["candidates"][0]["handle"]}
                )
                for key, value in [("orphan", one), ("second", two)]
            ],
            return_exceptions=True,
        )
        assert sum(isinstance(value, APIError) for value in results) == 1
        assert sum(history.session_id_for(key) == "chosen" for key in ["orphan", "second"]) == 1

    asyncio.run(run())


def test_detach_only_reverses_adoption_and_keeps_transcript_and_hook_barrier(recovery):
    service, cwd = recovery
    path = transcript(cwd)

    async def run():
        listing = await service.list_candidates("orphan")
        attached = await service.attach(
            "orphan",
            {"revision": listing["revision"], "handle": listing["candidates"][0]["handle"]},
        )
        before = path.read_bytes()
        detached = await service.detach("orphan", {"revision": attached["revision"]})
        assert detached["status"] == "missing" and detached["canAdopt"]
        assert service.server.history.native_identity("orphan") == {
            "native_id": None,
            "source": "none",
            "status": "missing",
        }
        assert path.read_bytes() == before
        control = service.server.history.restart_control("orphan")
        assert control["native_detach"]["at"] > 0
        assert control["native_binding"]["generation"]
        # The old ID cannot reappear through an ordinary late legacy hook.
        service.server.history.record(
            {
                "_id": uuid.uuid4().hex,
                "_ts": 9,
                "session_key": "orphan",
                "session_id": "chosen",
                "event_type": "Notification",
                "test": True,
            }
        )
        assert service.server.history.native_identity("orphan") == {
            "native_id": None,
            "source": "none",
            "status": "missing",
        }
        control["native_binding"].update(native_id="assigned", source="assigned")
        service.server.history.set_restart_control("orphan", control)
        identity = await service.identity("orphan")
        with pytest.raises(APIError):
            await service.detach("orphan", {"revision": identity["revision"]})
        assert service.server.history.session_id_for("orphan") == "assigned"

    asyncio.run(run())
