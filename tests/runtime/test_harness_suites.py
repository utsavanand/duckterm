"""Installable harnesses: register a suite by path, list it, run its installer
against a target directory. The fixture suite's install.sh drops a marker in
the target so the test proves the installer really ran there."""

import asyncio
import json
import os
import stat
from pathlib import Path

from tests.runtime.test_agents_md import _request

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def _make_suite(root: Path, *, manifest: bool) -> Path:
    suite = root / "kit"
    suite.mkdir()
    script = suite / "install.sh"
    # Writes into $PWD — the run_install contract sets cwd to the TARGET.
    script.write_text('#!/bin/sh\necho "kit for $(basename "$PWD")"\ntouch installed.marker\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    if manifest:
        (suite / "duckterm-harness.json").write_text(
            json.dumps(
                {
                    "name": "kit",
                    "description": "skills and hooks for tests",
                    "install": ["./install.sh"],
                }
            )
        )
    return suite


async def _post(port: int, token: str, path: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    return await _request(
        port,
        f"POST {path} HTTP/1.1\r\nHost: x\r\n".encode()
        + b"X-Duckterm-Token: "
        + token.encode()
        + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body,
    )


def test_register_list_install_roundtrip(tmp_path: Path) -> None:
    suite = _make_suite(tmp_path, manifest=True)
    target = tmp_path / "proj"
    target.mkdir()
    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> tuple[dict, dict, dict]:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            _, reg = await _post(port, server.token, "/harnesses/register", {"path": str(suite)})
            _, listed = await _request(port, b"GET /harnesses HTTP/1.1\r\nHost: x\r\n\r\n")
            _, installed = await _post(
                port, server.token, "/harnesses/kit/install", {"dir": str(target)}
            )
        return reg, listed, installed

    reg, listed, installed = asyncio.run(scenario())
    assert reg["name"] == "kit"
    assert listed["harnesses"][0]["description"] == "skills and hooks for tests"
    assert installed["ok"] is True
    assert "kit for proj" in installed["output"]
    assert (target / "installed.marker").is_file()  # ran with cwd=target


def test_install_sh_fallback_needs_no_manifest(tmp_path: Path) -> None:
    suite = _make_suite(tmp_path, manifest=False)
    target = tmp_path / "proj"
    target.mkdir()
    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> dict:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            _, reg = await _post(port, server.token, "/harnesses/register", {"path": str(suite)})
            assert reg["name"] == "kit"  # falls back to the directory name
            _, installed = await _post(
                port, server.token, "/harnesses/kit/install", {"dir": str(target)}
            )
        return installed

    installed = asyncio.run(scenario())
    assert installed["ok"] is True
    assert (target / "installed.marker").is_file()


def test_uninstall_runs_declared_command_and_choices_are_listed(tmp_path: Path) -> None:
    """A manifest can declare an uninstaller and picker choices; uninstall runs
    the declared command, and the list exposes both to the dashboard."""
    suite = tmp_path / "kit"
    suite.mkdir()
    for script, body in (
        ("install.sh", "#!/bin/sh\ntouch installed.marker\n"),
        ("uninstall.sh", "#!/bin/sh\nrm -f installed.marker\necho removed\n"),
    ):
        p = suite / script
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    (suite / "duckterm-harness.json").write_text(
        json.dumps(
            {
                "name": "kit",
                "install": ["./install.sh"],
                "uninstall": ["./uninstall.sh"],
                "args_choices": {"--persona": ["sport", "professional"]},
            }
        )
    )
    target = tmp_path / "proj"
    target.mkdir()
    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> tuple[dict, dict]:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            await _post(port, server.token, "/harnesses/register", {"path": str(suite)})
            _, listed = await _request(port, b"GET /harnesses HTTP/1.1\r\nHost: x\r\n\r\n")
            await _post(port, server.token, "/harnesses/kit/install", {"dir": str(target)})
            assert (target / "installed.marker").is_file()
            _, uninstalled = await _post(
                port, server.token, "/harnesses/kit/uninstall", {"dir": str(target)}
            )
        return listed, uninstalled

    listed, uninstalled = asyncio.run(scenario())
    entry = listed["harnesses"][0]
    assert entry["uninstallable"] is True
    assert entry["args_choices"] == {"--persona": ["sport", "professional"]}
    assert uninstalled["ok"] is True
    assert "removed" in uninstalled["output"]
    assert not (target / "installed.marker").exists()


def test_uninstall_without_declared_command_is_rejected(tmp_path: Path) -> None:
    suite = _make_suite(tmp_path, manifest=True)  # install only
    target = tmp_path / "proj"
    target.mkdir()
    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> int:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            await _post(port, server.token, "/harnesses/register", {"path": str(suite)})
            status, _ = await _post(
                port, server.token, "/harnesses/kit/uninstall", {"dir": str(target)}
            )
        return status

    assert asyncio.run(scenario()) == 400


def test_register_rejects_a_directory_with_no_installer(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> int:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            status, _ = await _post(port, server.token, "/harnesses/register", {"path": str(empty)})
        return status

    assert asyncio.run(scenario()) == 400


def test_failed_installer_reports_output(tmp_path: Path) -> None:
    suite = tmp_path / "broken"
    suite.mkdir()
    script = suite / "install.sh"
    script.write_text('#!/bin/sh\necho "boom: missing dependency" >&2\nexit 3\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    target = tmp_path / "proj"
    target.mkdir()
    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> tuple[int, dict]:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            await _post(port, server.token, "/harnesses/register", {"path": str(suite)})
            return await _post(
                port, server.token, "/harnesses/broken/install", {"dir": str(target)}
            )

    status, body = asyncio.run(scenario())
    assert status == 502
    assert body["ok"] is False
    assert "boom: missing dependency" in body["output"]


def test_installer_env_is_the_users(tmp_path: Path) -> None:
    """The installer runs as the user with their env — uv-suite's install.sh
    reads $HOME. Pin that PATH/HOME pass through."""
    suite = tmp_path / "envkit"
    suite.mkdir()
    script = suite / "install.sh"
    script.write_text('#!/bin/sh\necho "home=$HOME"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    target = tmp_path / "proj"
    target.mkdir()
    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> dict:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            await _post(port, server.token, "/harnesses/register", {"path": str(suite)})
            _, installed = await _post(
                port, server.token, "/harnesses/envkit/install", {"dir": str(target)}
            )
        return installed

    installed = asyncio.run(scenario())
    assert installed["output"] == f"home={os.environ['HOME']}"
