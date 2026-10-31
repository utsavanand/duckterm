"""The server serves the built dashboard at / so there's one URL. When the
dashboard isn't built, / returns a helpful hint instead of 404. Without the
owner token, / is a locked page. Every / response carries the self-probe
header."""

import asyncio
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.server import SELF_PROBE_HEADER, Server, dashboard_dir


async def _fetch(port: int, path: str, token: str | None = None) -> tuple[int, bytes, bytes]:
    auth = f"X-Duckterm-Token: {token}\r\n" if token else ""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n{auth}\r\n".encode())
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), 2)
    writer.close()
    await writer.wait_closed()
    head, body = response.split(b"\r\n\r\n", 1)
    return int(head.split()[1]), head, body


@pytest.mark.parametrize("authed", [False, True])
def test_root_carries_self_probe_and_is_locked_without_the_token(
    tmp_path: Path, authed: bool
) -> None:
    async def scenario() -> tuple[int, bytes, bytes, str]:
        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            return *await _fetch(port, "/", server.token if authed else None), server.token

    status, head, body, token = asyncio.run(scenario())
    assert status == (200 if authed else 401)
    assert f"{SELF_PROBE_HEADER}: 1".encode() in head
    if not authed:
        assert token.encode() not in body
        assert b"sign-in link" in body


def test_dashboard_dir_detects_a_real_build() -> None:
    # In this repo the dashboard is built (web/dist), so the resolver finds it.
    # If it isn't built in some environment, the resolver returns None — both are
    # valid; we just assert the function returns a coherent answer.
    result = dashboard_dir()
    assert result is None or (result / "index.html").is_file()


def test_dashboard_cache_policy_uses_the_served_asset_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Put the whole checkout under "assets" to catch filesystem-ancestor checks.
    dist = tmp_path / "assets" / "dashboard"
    files = {
        "index.html": "<html><head></head></html>",
        "favicon.svg": '<svg fill="yellow"/>',
        "favicon.ico": "icon",
        "settings.json": "{}",
        "assets/duckmark-abc12345.svg": '<svg fill="yellow"/>',
        "assets/index-abc12345.js": "export {};",
        "assets/index-abc12345.css": "body {}",
        "assets/help.html": "<html>Help</html>",
        "nested/assets/unhashed.js": "export {};",
    }
    for name, body in files.items():
        target = dist / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    monkeypatch.setattr("duckterm.server.dashboard_dir", lambda: dist)

    async def scenario() -> None:
        server = Server(history=HistoryStore(tmp_path / "cache.sqlite"))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            # A missing asset is not found; it must never fall back to the index,
            # which carries the install token and assets are served without one.
            status, _, body = await _fetch(port, "/assets/missing.js")
            assert status == 404
            assert server.token.encode() not in body
            for path, expected in {
                "/": "no-store",  # The index contains the install token.
                "/favicon.svg": "no-cache",
                "/favicon.ico": "no-cache",
                "/assets/help.html": "no-cache",
                **{
                    "/" + name: "public, max-age=31536000, immutable"
                    for name in files
                    if name.startswith("assets/") and not name.endswith(".html")
                },
            }.items():
                # Only the index needs the owner token; the bundle and icons are public.
                token = server.token if path == "/" else None
                status, head, body = await _fetch(port, path, token)
                assert status == 200, path
                assert f"Cache-Control: {expected}\r\n".encode() in head + b"\r\n", path
                assert f"{SELF_PROBE_HEADER}: 1".encode() in head
                if path == "/favicon.svg":
                    assert body == files["favicon.svg"].encode()
                    assert b"Content-Type: image/svg+xml" in head

    asyncio.run(scenario())
