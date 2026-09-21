"""Exercise browser and filesystem trust boundaries with harmless local fixtures."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from duckterm.agents import terminal
from duckterm.helpers import security
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


async def request(server: Server, raw: bytes) -> bytes:
    listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    async with listener:
        port = listener.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(raw)
            await writer.drain()
            return await asyncio.wait_for(reader.read(), 3)
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.mark.parametrize("value", ["$(printf${IFS}INJECTED)", "a;printf${IFS}INJECTED", "", "a\nb"])
def test_terminal_arguments_remain_literal(value: str) -> None:
    result = subprocess.run(
        ["sh", "-c", "printf '%s' " + terminal._q(value)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == value


def test_foreign_host_cannot_read_dashboard(tmp_path: Path) -> None:
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
    response = asyncio.run(request(server, b"GET / HTTP/1.1\r\nHost: attacker.test\r\n\r\n"))
    assert response.startswith(b"HTTP/1.1 403")
    assert server.token.encode() not in response


def test_other_localhost_port_cannot_open_websocket(tmp_path: Path) -> None:
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
    raw = (
        b"GET /ws HTTP/1.1\r\nHost: localhost:4300\r\n"
        b"Origin: http://localhost:8000\r\nUpgrade: websocket\r\n"
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
    )

    # Read just the handshake, since a vulnerable server keeps /ws open.
    async def scenario() -> bytes:
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        async with listener:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", listener.sockets[0].getsockname()[1]
            )
            writer.write(raw)
            await writer.drain()
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            writer.close()
            await writer.wait_closed()
            return head

    assert asyncio.run(scenario()).startswith(b"HTTP/1.1 403")


def test_dashboard_does_not_serve_prefix_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<head></head>Dashboard")
    (dist / "assets").mkdir()
    sibling = tmp_path / "dist-private"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("PRIVATE_MARKER")
    monkeypatch.setattr("duckterm.server.dashboard_dir", lambda: dist)
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
    response = asyncio.run(
        request(
            server, b"GET /assets/../../dist-private/secret.txt HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
    )
    assert b"PRIVATE_MARKER" not in response


@pytest.mark.parametrize("action", ["read", "write", "suggest"])
def test_agents_md_cannot_follow_symlink_outside_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("PRIVATE_MARKER")
    (allowed / "AGENTS.md").symlink_to(secret)
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
    monkeypatch.setattr(
        server, "_agents_md_dir_allowed", lambda p: Path(p).resolve().is_relative_to(allowed)
    )
    if action == "read":
        raw = f"GET /agents-md?dir={allowed} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
    else:
        endpoint = "/agents-md/suggest" if action == "suggest" else "/agents-md"
        body = json.dumps({"dir": str(allowed), "text": "OVERWRITTEN"}).encode()
        raw = (
            f"POST {endpoint} HTTP/1.1\r\nHost: localhost\r\n"
            f"X-Duckterm-Token: {server.token}\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode() + body
    response = asyncio.run(request(server, raw))
    assert response.startswith(b"HTTP/1.1 400")
    assert b"PRIVATE_MARKER" not in response
    assert secret.read_text() == "PRIVATE_MARKER"


def test_empty_stored_token_does_not_disable_authentication() -> None:
    assert security.token_valid({}, "") is False


@pytest.mark.parametrize(
    ("header", "status"),
    [
        (b"Content-Length: 999999999", 413),
        (b"Content-Length: -1", 413),
        (b"Content-Length: invalid", 400),
        (b"Content-Length: 0\r\nContent-Length: 5", 400),
        (b"Host: attacker.test", 400),
        (b"Transfer-Encoding: chunked", 400),
        (b"X-Large: " + b"a" * 33000, 400),
    ],
)
def test_request_limits_reject_before_reading_body(
    tmp_path: Path, header: bytes, status: int
) -> None:
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
    raw = b"POST /events HTTP/1.1\r\nHost: localhost\r\n" + header + b"\r\n\r\n"
    response = asyncio.run(request(server, raw))
    assert response.startswith(f"HTTP/1.1 {status}".encode())


def test_incomplete_request_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("duckterm.server.REQUEST_TIMEOUT_SECONDS", 0.02)
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
    response = asyncio.run(request(server, b"GET / HTTP/1.1\r\n"))
    assert response.startswith(b"HTTP/1.1 400")


def test_non_loopback_bind_is_refused_before_startup(tmp_path: Path) -> None:
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
    with pytest.raises(ValueError, match="loopback"):
        asyncio.run(server.serve("0.0.0.0", 0))


def test_empty_token_file_is_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    (tmp_path / "token").write_text("")
    token = security.load_or_create_token()
    assert len(token) >= 32
    assert security.token_valid({}, token) is False


@pytest.mark.parametrize("value", ["key\n", "/dev/ttys001\n", "snap-123\n"])
def test_validators_reject_trailing_newlines(value: str) -> None:
    assert not any(
        validator(value)
        for validator in (
            security.valid_session_key,
            security.valid_tty,
            security.valid_snapshot_id,
        )
    )
