"""Connector endpoints: list status, enable writes both harness configs,
disable cleans up. HOME/PATH are fully isolated — these tests must never
touch the developer's real ~/.claude.json or ~/.codex/config.toml."""

import asyncio
import json
import tomllib
from pathlib import Path

import pytest

from duckterm import connectors
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
    monkeypatch.setattr(connectors, "github_identity", lambda token: "test-user")
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
    assert github["credential"] is None
    assert github["enabled"] is False

    w = _W()
    asyncio.run(server._enable_connector(w, "github", b'{"source":"gh-cli"}'))
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


def test_huggingface_anonymous_lifecycle_over_endpoints(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duckterm import connectors

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(connectors, "huggingface_server_argv", lambda: ["npx"])
    server = _server(tmp_path)
    w = _W()
    asyncio.run(server._enable_connector(w, "huggingface", b"{}"))
    status, body = _status_and_body(w)
    assert (status, body["enabled"], body["credential"]) == (200, True, "anonymous")
    w = _W()
    asyncio.run(server._disable_connector(w, "huggingface"))
    status, body = _status_and_body(w)
    assert (status, body["enabled"]) == (200, False)


def test_slow_connector_probe_is_shared_and_one_cancel_does_not_cancel_others(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_status():
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return [{"name": "github"}]

    monkeypatch.setattr(connectors, "list_status", slow_status)
    server = _server(tmp_path)

    async def run():
        writers = [_W() for _ in range(8)]
        tasks = [asyncio.create_task(server._list_connectors(w)) for w in writers]
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.sleep(0)
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            assert len(calls) == 1
            # The event loop stays available while the single CLI probe waits.
            await asyncio.wait_for(asyncio.sleep(0.01), 0.5)
            release.set()
            await asyncio.gather(*tasks[1:])
            assert all(_status_and_body(w)[0] == 200 for w in writers[1:])
            await server._list_connectors(_W())
            assert len(calls) == 2  # Explicit later refresh is not a stale cache.
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            server.history.close()
            server.digests.close()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["enable", "disable", "forget"])
def test_connector_change_does_not_reuse_a_pre_change_probe(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    import threading

    entered, release = threading.Event(), threading.Event()
    calls = []

    def status():
        calls.append(True)
        old = len(calls) == 1
        if old:
            entered.set()
            assert release.wait(5)
        return [{"name": "github", "old": old}]

    monkeypatch.setattr(connectors, "list_status", status)
    monkeypatch.setattr(connectors, action, lambda *args, **kwargs: {"name": "github"})
    server = _server(tmp_path)

    async def run():
        old = asyncio.create_task(server._list_connectors(_W()))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            method = getattr(server, f"_{action}_connector")
            await method(_W(), "github", *([b"{}"] if action == "enable" else []))
            current = _W()
            await asyncio.wait_for(server._list_connectors(current), 1)
            assert _status_and_body(current)[1]["connectors"][0]["old"] is False
        finally:
            release.set()
            await old
            server.history.close()
            server.digests.close()

    asyncio.run(run())
