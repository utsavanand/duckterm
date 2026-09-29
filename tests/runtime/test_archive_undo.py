"""The archive undo boundary must precede every destructive side effect."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from duckterm import archives
from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def setup(tmp_path: Path) -> Server:
    history = HistoryStore(tmp_path / "archive.sqlite")
    history.record(
        {
            "event_type": "SessionStart",
            "session_key": "agent",
            "launched": True,
            "runtime": "generic",
            "test": True,
            "_ts": int(time.time() * 1000),
            "_id": "start",
        }
    )
    history.set_meta("agent", name="Archive fixture", group="project/backend", notes="Keep notes")
    history.set_pinned("agent", True)
    history.session_api.enroll("agent", {"root": "project", "purpose": "Keep membership"})
    server = Server(history=history)
    server.orchestrator.stop = AsyncMock(return_value=True)
    server.approvals.drop_session = Mock()
    return server


def test_undo_preserves_process_state_folder_enrollment_pins_and_approvals(tmp_path):
    async def scenario():
        server = setup(tmp_path)
        before = server.history.session("agent")
        member = dict(
            server.history._conn.execute(
                "SELECT * FROM session_api_members WHERE session_key='agent'"
            ).fetchone()
        )
        request = server.archives.request("agent")
        assert (
            server.archives.request("agent") == request
        )  # duplicate request keeps original deadline
        server.archives.cancel("agent", request["id"])
        await server.archives.close()
        assert server.history.session("agent") == before
        assert (
            dict(
                server.history._conn.execute(
                    "SELECT * FROM session_api_members WHERE session_key='agent'"
                ).fetchone()
            )
            == member
        )
        server.orchestrator.stop.assert_not_called()
        server.approvals.drop_session.assert_not_called()
        server.history.delete_session("agent")
        server.history.close()

    asyncio.run(scenario())


def test_expiry_runs_the_real_archive_path_and_rejects_late_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "UNDO_MS", 20)

    async def scenario():
        server = setup(tmp_path)
        request = server.archives.request("agent")
        await server.archives.tasks["agent"]
        assert server.history.session("agent")["state"] == "archived"
        assert server.history.session("agent")["grp"] == "project/backend"
        assert server.history.session("agent")["pinned"] == 1
        server.orchestrator.stop.assert_awaited_once_with("agent")
        server.approvals.drop_session.assert_called_once_with("agent")
        assert server.history.archive_requests() == []
        with pytest.raises(APIError, match="no longer pending"):
            server.archives.cancel("agent", request["id"])
        server.history.delete_session("agent")
        server.history.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("claimed", [False, True])
def test_quit_recovers_pending_and_partly_committed_archive(tmp_path, claimed):
    async def scenario():
        server = setup(tmp_path)
        request = server.archives.request("agent")
        await server.archives.close()  # quitting must leave an acknowledged request durable
        request.update(deadline=0, status="committing" if claimed else "pending")
        server.history.set_archive_request("agent", request)
        server.history.close()
        reopened = Server(history=HistoryStore(tmp_path / "archive.sqlite"))
        reopened.orchestrator.stop = AsyncMock(return_value=True)
        assert reopened.history.archive_request("agent")["id"] == request["id"]
        reopened.archives.recover()
        await reopened.archives.tasks["agent"]
        assert reopened.history.session("agent")["state"] == "archived"
        assert reopened.history.archive_requests() == []
        reopened.orchestrator.stop.assert_awaited_once()
        reopened.history.delete_session("agent")
        reopened.history.close()

    asyncio.run(scenario())


def test_stale_cancel_cannot_cancel_a_new_request_and_deadline_is_enforced(tmp_path):
    async def scenario():
        server = setup(tmp_path)
        first = server.archives.request("agent")
        server.archives.cancel("agent", first["id"])
        second = server.archives.request("agent")
        with pytest.raises(APIError, match="no longer pending"):
            server.archives.cancel("agent", first["id"])
        second["deadline"] = 0
        server.history.set_archive_request("agent", second)
        with pytest.raises(APIError, match="expired"):
            server.archives.cancel("agent", second["id"])
        await server.archives.close()
        server.history.delete_session("agent")
        server.history.close()

    asyncio.run(scenario())


class Writer:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass


def test_owner_auth_and_lifecycle_races_are_rejected(tmp_path):
    async def scenario():
        server = setup(tmp_path)

        async def call(method, path, headers, body=b"{}"):
            writer = Writer()
            await server._dispatch(method, path, asyncio.StreamReader(), writer, headers, body)
            status = int(writer.data.split(b" ")[1])
            return status, json.loads(writer.data.split(b"\r\n\r\n", 1)[1])

        path = "/sessions/agent/archive-undo"
        assert (await call("POST", path, {}))[0] == 401
        assert (await call("POST", path, {"authorization": "Bearer agent"}))[0] == 403
        owner = {"x-duckterm-token": server.token}
        status, request = await call("POST", path, owner)
        assert status == 200
        for action in ("stop", "resume", "archive"):
            assert (await call("POST", f"/sessions/agent/{action}", owner))[0] == 409
        assert (await call("DELETE", "/sessions/agent", owner))[0] == 409
        assert (await call("POST", "/sessions/agent/restart", owner))[0] == 409
        assert (await call("DELETE", path, owner, json.dumps({"id": request["id"]}).encode()))[
            0
        ] == 200
        server.orchestrator.stop.assert_not_called()
        await server.archives.close()
        server.history.delete_session("agent")
        server.history.close()

    asyncio.run(scenario())


def test_shutdown_during_stop_recovers_the_claimed_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "UNDO_MS", 0)

    async def scenario():
        server = setup(tmp_path)
        entered = asyncio.Event()

        async def stopping(key):
            entered.set()
            await asyncio.Event().wait()

        server.orchestrator.stop = AsyncMock(side_effect=stopping)
        request = server.archives.request("agent")
        await asyncio.wait_for(entered.wait(), 1)
        assert server.history.archive_request("agent")["status"] == "committing"
        with pytest.raises(APIError, match="expired"):
            server.archives.cancel("agent", request["id"])
        await server.archives.close()
        server.history.close()
        reopened = Server(history=HistoryStore(tmp_path / "archive.sqlite"))
        reopened.orchestrator.stop = AsyncMock(return_value=True)
        reopened.archives.recover()
        await reopened.archives.tasks["agent"]
        assert reopened.history.session("agent")["state"] == "archived"
        assert reopened.history.archive_requests() == []
        reopened.history.delete_session("agent")
        reopened.history.close()

    asyncio.run(scenario())


def test_failed_stop_stays_durable_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "UNDO_MS", 0)

    async def scenario():
        server = setup(tmp_path)
        failed = asyncio.Event()

        async def stopping(key):
            if not failed.is_set():
                failed.set()
                raise RuntimeError("temporary stop failure")
            return True

        server.orchestrator.stop = AsyncMock(side_effect=stopping)
        server.archives.request("agent")
        await asyncio.wait_for(failed.wait(), 1)
        assert server.history.archive_request("agent")["error"] == "temporary stop failure"
        assert server.history.session("agent")["state"] != "archived"
        await asyncio.wait_for(server.archives.tasks["agent"], 3)
        assert server.history.session("agent")["state"] == "archived"
        assert server.history.archive_requests() == []
        assert server.orchestrator.stop.await_count == 2
        server.history.delete_session("agent")
        server.history.close()

    asyncio.run(scenario())
