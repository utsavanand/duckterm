"""GET /connectors requires the owner token.

Reads are otherwise open so the browser can load the UI, but this one's body
names which providers the owner connected, under which identity, and when each
was last used. Loopback binding is not a substitute for the token: any local
process can reach the port.
"""

import asyncio
import json
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


class Writer:
    def __init__(self) -> None:
        self.data = b""

    def write(self, chunk: bytes) -> None:
        self.data += chunk

    async def drain(self) -> None:
        pass


@pytest.fixture()
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    (tmp_path / "home").mkdir()
    history = HistoryStore(tmp_path / "db.sqlite")
    instance = Server(history=history)
    yield instance
    history.close()


async def _get(server: Server, headers: dict[str, str]) -> tuple[int, dict]:
    writer = Writer()
    await server._dispatch("GET", "/connectors", asyncio.StreamReader(), writer, headers, b"")
    head, body = writer.data.split(b"\r\n\r\n", 1)
    return int(head.split()[1]), json.loads(body)


def test_the_owner_token_is_accepted(server: Server) -> None:
    status, body = asyncio.run(
        _get(server, {"host": "127.0.0.1", "x-duckterm-token": server.token})
    )
    assert status == 200
    assert isinstance(body["connectors"], list)


def test_a_request_with_no_token_is_refused(server: Server) -> None:
    status, body = asyncio.run(_get(server, {"host": "127.0.0.1"}))
    assert status == 401
    assert "token" in body["error"]


def test_a_wrong_token_is_refused(server: Server) -> None:
    status, _ = asyncio.run(
        _get(server, {"host": "127.0.0.1", "x-duckterm-token": "not-the-token"})
    )
    assert status == 401


def test_an_agent_credential_cannot_read_the_owner_s_connectors(server: Server) -> None:
    """A session token scopes an agent; it must not reach owner routes even
    when the agent also presents the owner token."""
    status, body = asyncio.run(
        _get(
            server,
            {
                "host": "127.0.0.1",
                "authorization": "Bearer agent-session-credential",
                "x-duckterm-token": server.token,
            },
        )
    )
    assert status == 403
    assert "owner routes" in body["error"]


def test_usage_fields_are_not_served_without_the_token(server: Server) -> None:
    """The point of the gate: last_used describes the owner's behaviour."""
    status, body = asyncio.run(_get(server, {"host": "127.0.0.1"}))
    assert status == 401
    assert "connectors" not in body
