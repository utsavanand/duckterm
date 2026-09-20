"""The request gate, end to end through the server: a cross-origin request is
refused (403), a state-changing request without the valid token is refused
(401), and a GET stays open (same-origin check only). This is the one test that
catches the auth gate silently dying — every OTHER runtime test sends the valid
token, so a weakened gate would leave them all green."""

import asyncio

from duckterm.helpers import security
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


async def _raw(port: int, request: bytes) -> str:
    """Send a raw HTTP/1.1 request and return the status line, so a test can set
    arbitrary headers (Origin, token) the high-level clients wouldn't."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    status_line = await asyncio.wait_for(reader.readline(), 3)
    writer.close()
    return status_line.decode("latin-1").strip()


def _run(tmp_path, request: bytes) -> str:  # type: ignore[no-untyped-def]
    async def scenario() -> str:
        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            return await _raw(port, request)

    return asyncio.run(scenario())


def test_cross_origin_request_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    req = (
        b"POST /sessions/launch HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Origin: http://evil.test\r\n"
        b"Content-Length: 0\r\n\r\n"
    )
    assert "403" in _run(tmp_path, req)


def test_state_change_without_token_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Same-origin (no Origin header) but no token → 401 on a non-GET.
    req = b"POST /sessions/launch HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n"
    assert "401" in _run(tmp_path, req)


def test_state_change_with_wrong_token_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    req = (
        b"POST /sessions/launch HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"X-Duckterm-Token: not-the-real-token\r\n"
        b"Content-Length: 0\r\n\r\n"
    )
    assert "401" in _run(tmp_path, req)


def test_get_is_open_without_a_token(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # GETs load the dashboard/read-only data; protected by same-origin, not token.
    req = b"GET /sessions HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
    assert "200" in _run(tmp_path, req)


def test_valid_token_passes_the_gate(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A correctly-tokened POST clears the gate (it may still 400 on a bad body —
    # that's past the gate, which is what we're asserting).
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    token = security.load_or_create_token()
    req = (
        b"POST /sessions/launch HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"X-Duckterm-Token: " + token.encode() + b"\r\n"
        b"Content-Length: 2\r\n\r\n{}"
    )
    status = _run(tmp_path, req)
    assert "401" not in status and "403" not in status  # cleared the gate
