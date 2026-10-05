"""Real private tmux shells must never become agent streams or identities."""

import asyncio
import json
import os
import shlex
import threading
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from tests.runtime.test_session_api import Writer, dispatch, enroll

from duckterm.agents import tmux
from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server
from duckterm.session_shells import SessionShells, target_for


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv(
        "DUCKTERM_TMUX_SOCKET", f"duckterm-pytest-shell-{os.getpid()}-{uuid.uuid4().hex}"
    )
    store = HistoryStore(tmp_path / "shell.sqlite")
    server = Server(history=store)
    monkeypatch.setattr(server, "_maybe_refresh_progress", lambda key: None)
    monkeypatch.setattr(server, "_relay_observe", lambda event: None)
    store.record(
        {
            "_id": "seed",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "shell-test",
            "launched": True,
            "test": True,
            "cwd": str(tmp_path),
            "runtime": "generic",
        }
    )
    yield server
    if tmux.has_tmux():
        tmux.kill_session("=" + target_for("shell-test"))
        tmux.kill_session("=" + tmux.target_for("shell-test"))
        tmux._tmux("kill-server")
    store.delete_session("shell-test")
    server.digests.close()
    store.close()


async def wait_for(predicate):
    for _ in range(100):
        if await predicate():
            return
        await asyncio.sleep(0.03)
    raise AssertionError("condition did not settle")


async def idle(shells):
    return not (await shells.status("shell-test"))["confirmation_required"]


def test_owner_routes_reject_agent_identity_and_overrides(rig, monkeypatch):
    owner = {"x-duckterm-token": rig.token}
    route = "/sessions/shell-test/shell"
    assert dispatch(rig, "GET", route, {})[0] == 401
    rig.history.set_meta("shell-test", group="work")
    peer = enroll(rig.history, "shell-test")
    assert dispatch(rig, "GET", route, peer)[0] == 403
    writer = Writer()

    async def cross_origin():
        await rig._dispatch(
            "POST",
            route,
            asyncio.StreamReader(),
            writer,
            {**owner, "origin": "https://evil.example"},
            b"",
        )

    asyncio.run(cross_origin())
    assert b"403" in writer.data.split(b"\r\n", 1)[0]
    assert dispatch(rig, "GET", "/sessions/remote-only/shell", owner)[0] == 404
    for data in [{"host": "remote"}, {"command": "echo bad"}, {"env": {}}, []]:
        assert dispatch(rig, "POST", route, owner, json.dumps(data).encode())[0] == 400
    assert dispatch(rig, "DELETE", route, owner, b'{"force":"yes"}')[0] == 400
    monkeypatch.setattr(tmux, "has_tmux", lambda: False)
    assert dispatch(rig, "GET", route, owner)[0] == 503


def test_unowned_and_archived_sessions_cannot_open(rig):
    async def scenario():
        for state in ["archived", "merged"]:
            rig.history.set_state("shell-test", state, now=10)
            with pytest.raises(APIError):
                await rig.shells.open("shell-test")
        with pytest.raises(APIError):
            await rig.shells.open("../other")

    asyncio.run(scenario())


def test_real_shell_stream_isolated_env_cwd_reconnect_and_close(rig, tmp_path, monkeypatch):
    if not tmux.has_tmux():
        pytest.skip("tmux unavailable")

    async def scenario():
        orch = rig.orchestrator
        await orch.launch(
            runtime=GenericRuntime("cat"), cwd=str(tmp_path), session_key="shell-test", test=True
        )
        sup = orch.get("shell-test")
        assert sup is not None
        try:
            # tmux retains globals from its creator, not just the current process.
            for name in ["DUCKTERM_SESSION_KEY", "DUCKTERM_SESSION_TOKEN_FILE", "DUCKTERM_URL"]:
                monkeypatch.setenv(name, "fake-session-secret")
                assert tmux._tmux("set-environment", "-g", name, "fake-session-secret")[0]
            assert tmux._tmux("set-environment", "-g", "HOME", str(tmp_path))[0]
            assert not (await rig.shells.status("shell-test"))["open"]
            first = await rig.shells.open("shell-test")
            await wait_for(lambda: idle(rig.shells))
            assert (await rig.shells.open("shell-test"))["pane_id"] == first["pane_id"]
            rig.shells = SessionShells(rig)  # server-side service recreation, no stored identity
            assert (await rig.shells.status("shell-test"))["pane_id"] == first["pane_id"]
            assert "shell-test-sh" not in await asyncio.to_thread(tmux.list_duckterm_sessions)
            assert "shell-test-sh" not in await orch.reconcile()
            assert rig.history.session("shell-test-sh") is None
            before = rig.history.events_for("shell-test")
            bus_before = rig.bus.recent()
            timeline_before = rig.history._conn.total_changes
            row = rig.history.session("shell-test")
            typing = sup.last_owner_input_ms
            output = tmp_path / "shell-env.json"
            command = (
                f"{shlex.quote(os.sys.executable)} -c "
                + shlex.quote(
                    "import os,json;json.dump({'cwd':os.getcwd(),"
                    "'env':{k:v for k,v in os.environ.items() "
                    "if k.startswith('DUCKTERM_')}},open(" + repr(str(output)) + ",'w'))"
                )
                + "; printf '\\nSHELL_ONLY_MARKER [busy] [idle]\\n'\n"
            )
            terminal = await rig.shells.terminal("shell-test")
            stream = terminal.subscribe_bytes()
            assert await asyncio.wait_for(anext(stream), 5)
            await terminal.write(command.encode())
            data = bytearray()
            while b"SHELL_ONLY_MARKER [busy] [idle]\r\n" not in data:
                data.extend(await asyncio.wait_for(anext(stream), 5))
            await stream.aclose()  # collapse/disconnect leaves shell alive
            await wait_for(lambda: idle(rig.shells))
            values = json.loads(output.read_text())
            assert values == {"cwd": str(tmp_path), "env": {"DUCKTERM_INTERNAL": "1"}}
            assert rig.history.events_for("shell-test") == before
            assert rig.bus.recent() == bus_before
            assert rig.history._conn.total_changes == timeline_before
            assert rig.history.session("shell-test") == row
            assert sup.last_owner_input_ms == typing
            assert not list(Path(os.environ["DUCKTERM_HOME"]).rglob("*shell-test-sh*.log"))
            assert orch.get("shell-test-sh") is None
            await terminal.write(b"sleep 60\n")

            async def sleeping():
                status = await rig.shells.status("shell-test")
                return status["confirmation_required"] and status["foreground"] == "sleep"

            await wait_for(sleeping)
            blocked = await rig.shells.close("shell-test")
            assert blocked["closed"] is False
            assert not (await rig.shells.close("shell-test", force=True, expected="stale"))[
                "closed"
            ]
            await terminal.write(b"\x03")
            await wait_for(lambda: idle(rig.shells))
            await terminal.write(b"sleep 60\n")
            await wait_for(sleeping)
            changed = await rig.shells.close(
                "shell-test", force=True, expected=blocked["confirmation_token"]
            )
            assert not changed["closed"]
            assert changed["confirmation_token"] != blocked["confirmation_token"]
            assert (
                await rig.shells.close(
                    "shell-test", force=True, expected=changed["confirmation_token"]
                )
            )["closed"]
            assert sup.running  # closing a shell must not kill the agent
            await rig.shells.open("shell-test")
            await orch.stop("shell-test")
            assert (await rig.shells.status("shell-test"))["open"]
            assert not tmux.session_exists(tmux.target_for("shell-test"))
            assert not tmux.send_raw(tmux.target_for("shell-test"), b"WRONG_TARGET")
            assert not tmux.capture_screen(tmux.target_for("shell-test"))
            await rig._commit_archive("shell-test")
            assert not await asyncio.to_thread(tmux.session_exists, "=" + target_for("shell-test"))
        finally:
            await orch.stop("shell-test")

    asyncio.run(scenario())


def test_cancelled_open_finishes_before_delete(rig, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    spawned = []

    def spawn(key, cwd):
        entered.set()
        assert release.wait(5)
        spawned.append(key)

    monkeypatch.setattr("duckterm.session_shells._spawn", spawn)
    monkeypatch.setattr(rig.shells, "status", AsyncMock(return_value={"open": False}))
    monkeypatch.setattr("duckterm.session_shells.inspect_shell", lambda key: {"open": False})

    async def scenario():
        opener = asyncio.create_task(rig.shells.open("shell-test"))
        await asyncio.to_thread(entered.wait, 5)
        opener.cancel()
        closer = asyncio.create_task(
            rig._teardown_session("shell-test", rig.history.session("shell-test"))
        )
        await asyncio.sleep(0.02)
        assert not closer.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await opener
        assert await closer
        assert spawned == ["shell-test"]

    asyncio.run(scenario())


def test_websocket_owner_subprotocol_auth(rig, monkeypatch):
    seen = []

    async def stream(reader, writer, headers, terminal, protocol=None):
        seen.append(protocol)

    monkeypatch.setattr(rig, "_stream_terminal", stream)
    monkeypatch.setattr(rig.shells, "terminal", AsyncMock(return_value=object()))

    async def scenario():
        headers = {"sec-websocket-protocol": "duckterm-shell, duckterm-owner." + rig.token}
        await rig._session_shell(
            asyncio.StreamReader(), Writer(), headers, "shell-test", True, "GET", b""
        )
        assert seen == ["duckterm-shell"]
        writer = Writer()
        await rig._session_shell(asyncio.StreamReader(), writer, {}, "shell-test", True, "GET", b"")
        assert b"401" in writer.data

    asyncio.run(scenario())


def test_real_owner_websocket_resize_disconnect_and_delete(rig, tmp_path):
    if not tmux.has_tmux():
        pytest.skip("tmux unavailable")
    from tests.runtime.test_terminal_flow import _mask_frame

    from duckterm.transport.websocket import read_frame

    async def scenario():
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        rig.history._conn.execute(
            "UPDATE sessions SET worktree_path=? WHERE session_key=?", (str(worktree), "shell-test")
        )
        rig.history._conn.commit()
        await rig.shells.open("shell-test")
        await wait_for(lambda: idle(rig.shells))
        listener = await asyncio.start_server(rig.handle, "127.0.0.1", 0)
        async with listener:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", listener.sockets[0].getsockname()[1]
            )
            writer.write(
                (
                    "GET /sessions/shell-test/shell/terminal HTTP/1.1\r\n"
                    "Host: localhost\r\nUpgrade: websocket\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Protocol: duckterm-shell, duckterm-owner."
                    + rig.token
                    + "\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            assert b"101 Switching" in headers
            assert b"Sec-WebSocket-Protocol: duckterm-shell\r\n" in headers
            writer.write(_mask_frame(1, b'{"resize":{"cols":93,"rows":27}}'))
            writer.write(_mask_frame(2, b"pwd; printf 'WS_%s\\n' MARKER\n"))
            await writer.drain()
            data = bytearray()
            while b"WS_MARKER\r\n" not in data:
                frame = await asyncio.wait_for(read_frame(reader), 5)
                assert frame is not None
                if frame[0] == 2:
                    data.extend(frame[1])
            assert str(worktree).encode() in data
            status = await rig.shells.status("shell-test")
            assert tmux._tmux(
                "display-message", "-p", "-t", status["pane_id"], "#{pane_width}x#{pane_height}"
            ) == (True, "93x27\n")
            writer.close()
            await writer.wait_closed()
        assert (await rig.shells.status("shell-test"))["open"]
        assert await rig._teardown_session("shell-test", rig.history.session("shell-test"))
        assert not tmux.session_exists(target_for("shell-test"))

    asyncio.run(scenario())


def test_watched_heartbeat_and_pending_archive_refuse_open(rig):
    async def scenario():
        for launched, heartbeat in [(0, 0), (1, 1)]:
            rig.history._conn.execute(
                "UPDATE sessions SET launched=?,heartbeat=? WHERE session_key='shell-test'",
                (launched, heartbeat),
            )
            rig.history._conn.commit()
            with pytest.raises(APIError, match="owned terminal"):
                await rig.shells.open("shell-test")
        rig.history._conn.execute(
            "UPDATE sessions SET launched=1,heartbeat=0 WHERE session_key='shell-test'"
        )
        rig.history._conn.commit()
        request = rig.archives.request("shell-test")
        with pytest.raises(APIError, match="awaiting archive"):
            await rig.shells.open("shell-test")
        rig.archives.cancel("shell-test", request["id"])
        await rig.archives.close()

    asyncio.run(scenario())
