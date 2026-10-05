"""Compatibility boundaries when owner shells share the agent tmux namespace."""

import asyncio

import pytest
from tests.runtime import test_session_shells as shell_fixtures

from duckterm.agents import tmux
from duckterm.runtimes.generic import GenericRuntime

rig = shell_fixtures.rig


@pytest.mark.parametrize("operation", ["archive", "delete"])
def test_reserved_shell_suffix_does_not_block_existing_agent_cleanup(rig, tmp_path, operation):
    key = "legacy-agent-sh"
    rig.history.record(
        {
            "_id": key,
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": key,
            "launched": True,
            "test": True,
            "cwd": str(tmp_path),
            "runtime": "generic",
        }
    )

    async def scenario():
        try:
            if operation == "archive":
                await rig._commit_archive(key)
                assert rig.history.session(key)["state"] == "archived"
            else:
                assert await rig._teardown_session(key, rig.history.session(key))
                assert rig.history.session(key) is None
        finally:
            rig.history.delete_session(key)

    asyncio.run(scenario())


def test_existing_agent_with_shell_suffix_remains_discoverable(rig, tmp_path):
    if not tmux.has_tmux():
        pytest.skip("tmux unavailable")

    async def scenario():
        key = "legacy-agent-sh"
        await rig.orchestrator.launch(
            runtime=GenericRuntime("cat"), cwd=str(tmp_path), session_key=key, test=True
        )
        try:
            assert key in tmux.list_duckterm_sessions()
        finally:
            await rig.orchestrator.stop(key)
            rig.history.delete_session(key)

    asyncio.run(scenario())


def test_real_http_shell_auth_rejects_query_tokens_and_foreign_origins(rig):
    async def scenario():
        listener = await asyncio.start_server(rig.handle, "127.0.0.1", 0)
        async with listener:
            port = listener.sockets[0].getsockname()[1]
            cases = [
                ("/sessions/shell-test/shell", {}, 401),
                ("/sessions/shell-test/shell", {"authorization": "Bearer synthetic"}, 403),
                ("/sessions/shell-test/shell/terminal", {}, 401),
                ("/sessions/shell-test/shell/terminal?token=" + rig.token, {}, 404),
                (
                    "/sessions/shell-test/shell/terminal",
                    {
                        "sec-websocket-protocol": "duckterm-shell, duckterm-owner." + rig.token,
                        "origin": "https://foreign.example",
                    },
                    403,
                ),
                ("/sessions/shell-test/shell", {"x-duckterm-token": rig.token}, 200),
                ("/sessions/remote-only/shell", {"x-duckterm-token": rig.token}, 404),
            ]
            for path, headers, expected in cases:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                request = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                request += "".join(f"{name}: {value}\r\n" for name, value in headers.items())
                writer.write((request + "\r\n").encode())
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), 5)
                assert int(response.split(b" ", 2)[1]) == expected
                writer.close()
                await writer.wait_closed()
        assert not (await rig.shells.status("shell-test"))["open"]

    asyncio.run(scenario())


def test_unrecognized_sibling_is_neither_adopted_nor_killed(rig, tmp_path):
    from duckterm.core.session_api import APIError

    if not tmux.has_tmux():
        pytest.skip("tmux unavailable")
    target = tmux.spawn("shell-test-sh", "cat", str(tmp_path))

    async def scenario():
        with pytest.raises(APIError, match="unrecognized"):
            await rig.shells.open("shell-test")
        with pytest.raises(APIError, match="unrecognized"):
            await rig.shells.close("shell-test", force=True, expected="anything")
        assert tmux.session_exists(target)

    asyncio.run(scenario())
