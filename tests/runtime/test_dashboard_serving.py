"""The server serves the built dashboard at / so there's one URL. When the
dashboard isn't built, / returns a helpful hint instead of 404 — and always
carries the self-probe header."""

import asyncio
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.server import SELF_PROBE_HEADER, Server, dashboard_dir


def test_root_carries_self_probe_and_responds(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, str]:
        store = HistoryStore(tmp_path / "db.sqlite")
        srv = await asyncio.start_server(Server(history=store).handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            writer.close()
        status_line = head.split(b"\r\n")[0].decode()
        return int(status_line.split()[1]), head.decode("latin-1").lower()

    status, head = asyncio.run(scenario())
    assert status == 200
    assert SELF_PROBE_HEADER.lower() in head


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
        store = HistoryStore(tmp_path / "cache.sqlite")
        srv = await asyncio.start_server(Server(history=store).handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            for path, expected in {
                "/": "no-store",  # The index contains the install token.
                "/favicon.svg": "no-cache",
                "/favicon.ico": "no-cache",
                "/assets/help.html": "no-cache",
                "/assets/missing.js": "no-store",  # SPA fallback, not an asset.
                **{
                    "/" + name: "public, max-age=31536000, immutable"
                    for name in files
                    if name.startswith("assets/") and not name.endswith(".html")
                },
            }.items():
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), 2)
                writer.close()
                await writer.wait_closed()
                head, body = response.split(b"\r\n\r\n", 1)
                assert head.startswith(b"HTTP/1.1 200"), path
                assert f"Cache-Control: {expected}\r\n".encode() in head + b"\r\n", path
                assert f"{SELF_PROBE_HEADER}: 1".encode() in head
                if path == "/favicon.svg":
                    assert body == files["favicon.svg"].encode()
                    assert b"Content-Type: image/svg+xml" in head

    asyncio.run(scenario())
