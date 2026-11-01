"""Every owner route refuses a caller without the owner credential.

The origin check stops browsers, not other local callers: another macOS user, a
sandboxed agent that can reach loopback, or an SSH-forwarded port. Before this
matrix, GETs were open: GET / served the owner token, GET /file read any file
under $HOME, and the agent terminal WebSocket accepted keystrokes. A route added
without auth fails here instead of shipping.
"""

import asyncio
import contextlib
import json
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.server import Route, Server

# Served to anyone: the locked page, the sign-in exchange, the bundle and icons.
PUBLIC = {
    ("GET", "/"),
    ("GET", "/assets/probe"),
    ("GET", "/favicon.svg"),
    ("GET", "/favicon.ico"),
}
# Routes matched by regex in _dispatch rather than the route table.
REGEX_ROUTES = [
    ("GET", "/sessions/probe/inbox"),
    ("GET", "/folders/probe/broadcast"),
    ("POST", "/folders/probe/broadcast"),
    ("GET", "/broadcasts/probe"),
    ("DELETE", "/broadcasts/probe"),
]


class Writer:
    def __init__(self) -> None:
        self.data = b""

    def write(self, chunk: bytes) -> None:
        self.data += chunk

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture()
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<head></head>Dashboard")
    monkeypatch.setattr("duckterm.server.dashboard_dir", lambda: dist)
    history = HistoryStore(tmp_path / "db.sqlite")
    instance = Server(history=history)
    yield instance
    history.close()


def concrete(route: Route) -> str:
    if route.prefix is not None:
        # "/sessions/" takes a segment; "/file" and "/browse" take a query.
        segment = "probe" if route.prefix.endswith("/") else ""
        return route.prefix + segment + (route.suffix or "")
    return route.matcher


def owner_routes(server: Server) -> list[tuple[str, str]]:
    table = {(r.method, concrete(r)) for r in server._routes()}
    return sorted((table | set(REGEX_ROUTES)) - PUBLIC)


async def send(
    server: Server, method: str, path: str, headers: dict[str, str]
) -> tuple[int, dict[str, str], bytes]:
    writer = Writer()
    # An open stream (/stream, /ws, a terminal) never returns: count it as open.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(
            server._dispatch(method, path, asyncio.StreamReader(), writer, headers, b"{}"), 2
        )
    head, _, body = writer.data.partition(b"\r\n\r\n")
    if not head:
        return 0, {}, b""
    lines = head.decode("latin-1").split("\r\n")
    fields = dict(line.split(": ", 1) for line in lines[1:])
    return int(lines[0].split()[1]), fields, body


def test_the_matrix_covers_the_routes_it_was_written_for(server: Server) -> None:
    routes = owner_routes(server)
    assert len(routes) > 100
    for route in [
        ("GET", "/file"),
        ("GET", "/browse"),
        ("GET", "/sessions/probe/terminal"),
        ("GET", "/stream"),
        ("GET", "/ws"),
    ]:
        assert route in routes


def test_every_owner_route_refuses_a_caller_without_credentials(server: Server) -> None:
    open_routes = []
    for method, path in owner_routes(server):
        status, _, body = asyncio.run(send(server, method, path, {"host": "127.0.0.1:4300"}))
        if status != 401 or server.token.encode() in body:
            open_routes.append((method, path, status))
    assert open_routes == []


def test_a_session_credential_is_not_an_owner_credential(server: Server) -> None:
    for method, path in [("GET", "/file"), ("GET", "/sessions/probe/terminal"), ("GET", "/")]:
        headers = {"host": "127.0.0.1:4300", "authorization": "Bearer session-token"}
        status, _, _ = asyncio.run(send(server, method, path, headers))
        assert status == 403


def test_the_locked_page_names_the_server_but_not_the_token(server: Server) -> None:
    status, fields, body = asyncio.run(send(server, "GET", "/", {"host": "127.0.0.1:4300"}))
    assert status == 401
    assert fields["X-Duckterm"] == "1"
    assert server.token.encode() not in body
    assert b"duckterm dashboard" in body


def test_the_owner_header_opens_the_dashboard(server: Server) -> None:
    headers = {"host": "127.0.0.1:4300", "x-duckterm-token": server.token}
    status, _, body = asyncio.run(send(server, "GET", "/", headers))
    assert status == 200
    assert f'<meta name="duckterm-token" content="{server.token}">'.encode() in body


def test_a_missing_asset_is_not_answered_with_the_dashboard(server: Server) -> None:
    status, _, body = asyncio.run(
        send(server, "GET", "/assets/missing.js", {"host": "127.0.0.1:4300"})
    )
    assert status == 404
    assert server.token.encode() not in body


def test_login_trades_the_token_for_a_port_scoped_http_only_cookie(server: Server) -> None:
    status, fields, _ = asyncio.run(
        send(server, "GET", f"/login?token={server.token}", {"host": "127.0.0.1:4300"})
    )
    assert status == 303
    assert fields["Location"] == "/"
    assert fields["Referrer-Policy"] == "no-referrer"
    assert fields["Set-Cookie"] == (
        f"duckterm_owner_4300={server.token}; Path=/; HttpOnly; SameSite=Strict"
    )


@pytest.mark.parametrize("supplied", ["", "wrong", "x" * 43])
def test_login_with_a_wrong_token_sets_nothing(server: Server, supplied: str) -> None:
    status, fields, body = asyncio.run(
        send(server, "GET", f"/login?token={supplied}", {"host": "127.0.0.1:4300"})
    )
    assert status == 401
    assert "Set-Cookie" not in fields
    assert server.token.encode() not in body


def read_file(server: Server, path: Path, headers: dict[str, str]) -> int:
    status, _, _ = asyncio.run(send(server, "GET", f"/file?path={path}", headers))
    return status


def test_the_cookie_for_this_port_reads_like_the_header(server: Server) -> None:
    secret = Path.home() / "notes.txt"
    secret.write_text("owner notes")
    cookie = f"other=1; duckterm_owner_4300={server.token}"
    assert read_file(server, secret, {"host": "127.0.0.1:4300", "cookie": cookie}) == 200


def test_another_ports_cookie_is_refused(server: Server) -> None:
    # Browsers share cookies across 127.0.0.1 ports; :4301 must not pass as :4300.
    secret = Path.home() / "notes.txt"
    secret.write_text("owner notes")
    cookie = f"duckterm_owner_4301={server.token}"
    assert read_file(server, secret, {"host": "127.0.0.1:4300", "cookie": cookie}) == 401


def test_a_malformed_cookie_header_is_refused_not_an_error(server: Server) -> None:
    secret = Path.home() / "notes.txt"
    secret.write_text("owner notes")
    headers = {"host": "127.0.0.1:4300", "cookie": 'duckterm_owner_4300="unterminated'}
    assert read_file(server, secret, headers) == 401


def test_the_owner_subprotocol_authenticates_the_agent_terminal(server: Server) -> None:
    headers = {
        "host": "127.0.0.1:4300",
        "sec-websocket-protocol": f"duckterm-owner.{server.token}",
        "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
    }
    status, _, body = asyncio.run(send(server, "GET", "/sessions/probe/terminal", headers))
    # Past the gate: no such session, rather than no credential.
    assert status == 404
    assert json.loads(body)["error"] == "no live session to attach"


def test_a_foreign_origin_is_refused_even_with_the_token(server: Server) -> None:
    headers = {
        "host": "127.0.0.1:4300",
        "origin": "http://127.0.0.1:8000",
        "x-duckterm-token": server.token,
    }
    status, _, _ = asyncio.run(send(server, "GET", "/sessions", headers))
    assert status == 403
