"""Connector endpoints: list status, enable writes both harness configs,
disable cleans up. HOME/PATH are fully isolated — these tests must never
touch the developer's real ~/.claude.json or ~/.codex/config.toml."""

import asyncio
import json
import tomllib
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


class _W:
    def __init__(self) -> None:
        self.data = b""

    def write(self, b: bytes) -> None:
        self.data += b

    async def drain(self) -> None:
        pass


def _status_and_body(w: _W) -> tuple[int, dict]:
    head, body = w.data.split(b"\r\n\r\n", 1)
    return int(head.split()[1]), json.loads(body)


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm-home"))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\necho "ghp_test_token"\n')
    gh.chmod(0o755)
    server_bin = bin_dir / "github-mcp-server"
    server_bin.write_text("#!/bin/sh\nexit 0\n")
    server_bin.chmod(0o755)
    return home


def _server(tmp_path: Path) -> Server:
    return Server(history=HistoryStore(tmp_path / "db.sqlite"))


def test_connectors_lifecycle_over_endpoints(isolated: Path, tmp_path: Path) -> None:
    server = _server(tmp_path)

    w = _W()
    asyncio.run(server._list_connectors(w))
    status, body = _status_and_body(w)
    assert status == 200
    github = next(c for c in body["connectors"] if c["name"] == "github")
    assert github["credential"] == "gh-cli"
    assert github["enabled"] is False

    w = _W()
    asyncio.run(server._enable_connector(w, "github", b"{}"))
    status, body = _status_and_body(w)
    assert (status, body["enabled"]) == (200, True)
    assert json.loads((isolated / ".claude.json").read_text())["mcpServers"]["github"]
    codex = tomllib.loads((isolated / ".codex" / "config.toml").read_text())
    assert "github" in codex["mcp_servers"]

    w = _W()
    asyncio.run(server._disable_connector(w, "github"))
    status, body = _status_and_body(w)
    assert (status, body["enabled"]) == (200, False)
    assert json.loads((isolated / ".claude.json").read_text())["mcpServers"] == {}


def test_enable_failure_maps_to_400_with_reason(isolated: Path, tmp_path: Path) -> None:
    server = _server(tmp_path)
    w = _W()
    asyncio.run(server._enable_connector(w, "railway", b"{}"))
    status, body = _status_and_body(w)
    assert status == 400
    assert "not installed" in body["error"]


def test_unknown_connector_is_404(isolated: Path, tmp_path: Path) -> None:
    server = _server(tmp_path)
    w = _W()
    asyncio.run(server._enable_connector(w, "gitlab", b"{}"))
    status, _ = _status_and_body(w)
    assert status == 404
