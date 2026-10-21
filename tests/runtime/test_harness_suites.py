"""Skill kits: register a kit by path, list it, read its contents. Duckterm
never runs a kit's own install script, so a legacy manifest that still names
one registers fine but nothing it names ever executes."""

import asyncio
import json
import stat
from pathlib import Path

from tests.runtime.test_agents_md import _request

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def _make_legacy_suite(root: Path) -> Path:
    """A kit whose manifest still declares the retired script keys, with an
    install.sh that would leave a marker if anything ran it."""
    suite = root / "kit"
    suite.mkdir()
    script = suite / "install.sh"
    script.write_text("#!/bin/sh\ntouch installed.marker\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (suite / "duckterm-harness.json").write_text(
        json.dumps(
            {
                "name": "kit",
                "description": "skills and hooks for tests",
                "install": ["./install.sh"],
                "uninstall": ["./install.sh"],
                "args_choices": {"--persona": ["sport"]},
            }
        )
    )
    return suite


async def _post(port: int, token: str, path: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    # These routes start a real worker/subprocess (production allows 300s).
    # Keep the synthetic installer bounded without making a two-second HTTP
    # helper deadline an accidental installer performance contract.
    timeout = 10 if path.endswith(("/install", "/uninstall")) else 2
    return await _request(
        port,
        f"POST {path} HTTP/1.1\r\nHost: localhost\r\n".encode()
        + b"X-Duckterm-Token: "
        + token.encode()
        + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body,
        timeout=timeout,
    )


async def _post_status(port: int, token: str, path: str) -> int:
    """POST and return only the status — unknown routes answer in plain text."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        f"POST {path} HTTP/1.1\r\nHost: localhost\r\nX-Duckterm-Token: {token}\r\n"
        "Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{}".encode()
    )
    await writer.drain()
    status = int((await reader.readline()).split()[1])
    writer.close()
    return status


def test_legacy_manifest_registers_but_its_scripts_never_run(tmp_path: Path) -> None:
    suite = _make_legacy_suite(tmp_path)
    target = tmp_path / "proj"
    target.mkdir()
    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> tuple[dict, dict, int, int]:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            _, reg = await _post(port, server.token, "/harnesses/register", {"path": str(suite)})
            _, listed = await _request(port, b"GET /harnesses HTTP/1.1\r\nHost: localhost\r\n\r\n")
            install = await _post_status(port, server.token, "/harnesses/kit/install")
            uninstall = await _post_status(port, server.token, "/harnesses/kit/uninstall")
        return reg, listed, install, uninstall

    reg, listed, install, uninstall = asyncio.run(scenario())
    assert reg["name"] == "kit"
    assert listed["harnesses"] == [
        {
            "name": "kit",
            "path": str(suite),
            "description": "skills and hooks for tests",
            "compatible": [],
        }
    ]
    assert (install, uninstall) == (404, 404)
    assert not (target / "installed.marker").exists()
    assert not (suite / "installed.marker").exists()


def test_register_rejects_a_folder_with_only_an_install_script(tmp_path: Path) -> None:
    kit = tmp_path / "scripted"
    kit.mkdir()
    (kit / "install.sh").write_text("#!/bin/sh\n")
    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> tuple[int, dict]:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            return await _post(port, server.token, "/harnesses/register", {"path": str(kit)})

    status, body = asyncio.run(scenario())
    assert status == 400
    assert "duckterm-harness.json" in body["error"]


def test_sessions_report_detected_skill_kit(tmp_path: Path) -> None:
    """A registered kit with a `detect` marker shows up on sessions whose
    folder has it — the 'Skill kit' row in the panel."""
    suite = tmp_path / "kit"
    suite.mkdir()
    (suite / "duckterm-harness.json").write_text(
        json.dumps({"name": "kit", "detect": ".claude/marker"})
    )
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "marker").write_text("")
    bare = tmp_path / "bare"
    bare.mkdir()

    store = HistoryStore(tmp_path / "db.sqlite")
    store.record(
        {
            "event_type": "SessionStart",
            "session_key": "with",
            "cwd": str(project),
            "launched": True,
            "_ts": 1,
            "_id": "a",
        }
    )
    store.record(
        {
            "event_type": "SessionStart",
            "session_key": "without",
            "cwd": str(bare),
            "launched": True,
            "_ts": 2,
            "_id": "b",
        }
    )

    async def scenario() -> list[dict]:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            await _post(port, server.token, "/harnesses/register", {"path": str(suite)})
            _, listed = await _request(port, b"GET /sessions HTTP/1.1\r\nHost: localhost\r\n\r\n")
        return list(listed["sessions"])

    rows = {r["session_key"]: r for r in asyncio.run(scenario())}
    assert rows["with"]["suites"] == ["kit (project)"]
    assert rows["without"]["suites"] == []


def test_harness_contents_lists_what_ships_with_descriptions(tmp_path: Path) -> None:
    """The contents endpoint reads the suite's own files: skills (frontmatter
    description, incl. YAML folded blocks), hooks (header comment), sub-agents
    — plus the manifest's compatibility list."""
    suite = tmp_path / "kit"
    (suite / "skills" / "ship-it").mkdir(parents=True)
    (suite / "skills" / "ship-it" / "SKILL.md").write_text(
        "---\nname: ship-it\ndescription: >\n  Reviews, tests,\n  then ships.\n---\nbody\n"
    )
    (suite / "hooks").mkdir()
    (suite / "hooks" / "guard.sh").write_text("#!/bin/sh\n# Blocks rm -rf outside the repo\n")
    (suite / "agents").mkdir()
    (suite / "agents" / "reviewer.md").write_text(
        "---\ndescription: Reviews diffs for correctness.\n---\n"
    )
    (suite / "duckterm-harness.json").write_text(
        json.dumps({"name": "kit", "compatible": ["claude-code"]})
    )

    store = HistoryStore(tmp_path / "db.sqlite")

    async def scenario() -> dict:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            await _post(port, server.token, "/harnesses/register", {"path": str(suite)})
            _, body = await _request(
                port, b"GET /harnesses/kit/contents HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
        return body

    body = asyncio.run(scenario())
    assert body["compatible"] == ["claude-code"]
    by_kind = {(i["kind"], i["name"]): i["description"] for i in body["contents"]}
    assert by_kind[("skill", "ship-it")] == "Reviews, tests, then ships."
    assert by_kind[("hook", "guard")] == "Blocks rm -rf outside the repo"
    assert by_kind[("sub-agent", "reviewer")] == "Reviews diffs for correctness."
