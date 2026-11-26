"""Real hook setup with disposable config, followed by accepted event evidence."""

import asyncio
import json
import os
import shlex
import shutil
import sys
from contextlib import closing
from pathlib import Path

import pytest
from tests.runtime.test_session_api import Writer

from duckterm.agents import hook_connection, hooks_install
from duckterm.core.orchestrator import SessionSupervisor
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.runtimes.codex import CodexRuntime
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server


@pytest.fixture
def isolated(tmp_path, monkeypatch, _no_real_hook_setup):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for key in list(os.environ):
        if key.startswith("DUCKTERM_") or key in {"CLAUDE_CONFIG_DIR", "CODEX_HOME"}:
            monkeypatch.delenv(key)
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    monkeypatch.setattr(hooks_install, "prepare_for_launch", _no_real_hook_setup)
    return tmp_path


@pytest.mark.parametrize(
    "runtime,config_var,name",
    [
        (ClaudeCodeRuntime, "CLAUDE_CONFIG_DIR", "settings.json"),
        (CodexRuntime, "CODEX_HOME", "hooks.json"),
    ],
)
def test_launch_configures_before_spawn_preserves_company_hooks_and_requires_event(
    isolated, monkeypatch, runtime, config_var, name
):
    root = isolated / "company-config"
    root.mkdir()
    config = root / name
    company = {"matcher": "Bash", "hooks": [{"type": "command", "command": "company-hook"}]}
    original = {"hooks": {"PreToolUse": [company]}, "permissions": {"deny": ["Read(secrets)"]}}
    config.write_text(json.dumps(original))
    with closing(HistoryStore(isolated / "history.sqlite")) as history:
        server = Server(history=history)
        spawned = []

        async def spawn(supervisor, argv):
            value = json.loads(config.read_text())
            assert value["hooks"]["PreToolUse"][0] == company
            assert value["permissions"] == original["permissions"]
            assert len(value["hooks"]["PreToolUse"]) == 2
            assert history.session(supervisor.session_key)
            spawned.append(supervisor)

        monkeypatch.setattr(SessionSupervisor, "_start_tmux", spawn)
        monkeypatch.setattr(SessionSupervisor, "_start_pty", spawn)

        async def run():
            key = await server.orchestrator.launch(
                runtime=runtime(shlex.quote(sys.executable)),
                cwd=str(isolated),
                env={config_var: str(root)},
                test=True,
            )
            row = history.session(key)
            evidence = hook_connection.read(row)
            now = evidence["configured_at"]
            assert hook_connection.status(row, now)["state"] == "awaiting"
            assert hook_connection.status(row, now + 30_000)["state"] == "unconfirmed"
            before = config.read_bytes()
            timestamp = config.stat().st_mtime_ns
            hooks_install.prepare_for_launch(runtime.name, isolated, {config_var: str(root)})
            assert config.read_bytes() == before
            assert config.stat().st_mtime_ns == timestamp
            # A synthetic event and a stale provider launch cannot confirm delivery.
            raw = {
                "session_key": key,
                "runtime": runtime.name,
                "event_type": "UserPromptSubmit",
                "session_id": history.native_identity(key)["native_id"] or "codex-conversation",
                "launch_generation": evidence["generation"],
            }
            server.bus.publish(raw)
            assert hook_connection.status(history.session(key), now)["state"] == "awaiting"
            await server._ingest(Writer(), json.dumps({**raw, "launch_generation": "old"}).encode())
            assert hook_connection.status(history.session(key), now)["state"] == "awaiting"
            await server._ingest(Writer(), json.dumps(raw).encode())
            assert hook_connection.status(history.session(key), now)["state"] == "connected"
            # Receipt survives a server restart; it is not a transient UI guess.
            with closing(HistoryStore(isolated / "history.sqlite")) as reopened:
                assert hook_connection.status(reopened.session(key), now)["state"] == "connected"
            await server.orchestrator.launch(
                runtime=runtime(shlex.quote(sys.executable)),
                cwd=str(isolated),
                session_key=key,
                env={config_var: str(root)},
                test=True,
            )
            assert hook_connection.status(history.session(key), now)["state"] == "awaiting"
            await server._ingest(Writer(), json.dumps(raw).encode())
            assert hook_connection.status(history.session(key), now)["state"] == "awaiting"

        try:
            asyncio.run(run())
            assert len(spawned) == 2
        finally:
            server.digests.close()
            history.purge_test_sessions()


def test_invalid_settings_abort_before_spawn_without_overwriting_config(isolated, monkeypatch):
    root = isolated / ".claude"
    root.mkdir()
    config = root / "settings.json"
    config.write_text('{"hooks": "broken", "company": true}')
    before = config.read_bytes()

    async def spawn(*args):
        pytest.fail("Agent must not launch when its hooks could not be configured")

    monkeypatch.setattr(SessionSupervisor, "_start_tmux", spawn)
    monkeypatch.setattr(SessionSupervisor, "_start_pty", spawn)
    with closing(HistoryStore(isolated / "history.sqlite")) as history:
        server = Server(history=history)
        try:
            with pytest.raises(ValueError, match="Could not configure DuckTerm hooks"):
                asyncio.run(
                    server.orchestrator.launch(
                        runtime=ClaudeCodeRuntime(shlex.quote(sys.executable)),
                        cwd=str(isolated),
                        session_key="bad",
                        test=True,
                    )
                )
            assert config.read_bytes() == before
            assert history.session("bad") is None
            assert server.orchestrator.get("bad") is None
        finally:
            server.digests.close()


def test_unsupported_runtime_does_not_create_config(isolated):
    assert hooks_install.prepare_for_launch(GenericRuntime.name, isolated, {}) is None
    assert not (isolated / ".duckterm-hooks").exists()


def test_disabled_hooks_policy_is_preserved_not_overridden(isolated):
    root = isolated / ".claude"
    root.mkdir()
    config = root / "settings.json"
    config.write_text('{"disableAllHooks": true, "allowManagedHooksOnly": true}')
    hooks_install.prepare_for_launch("claude-code", isolated, {})
    saved = json.loads(config.read_text())
    assert saved["disableAllHooks"] is True
    assert saved["allowManagedHooksOnly"] is True


def test_symlink_settings_refused_and_target_preserved(isolated):
    target = isolated / "company-settings.json"
    target.write_text('{"company": true}')
    (isolated / ".claude").mkdir()
    (isolated / ".claude/settings.json").symlink_to(target)
    with pytest.raises(ValueError, match="Could not configure DuckTerm hooks"):
        hooks_install.prepare_for_launch("claude-code", isolated, {})
    assert target.read_text() == '{"company": true}'


def test_installed_hook_reaches_authenticated_local_server(isolated, monkeypatch):
    if not shutil.which("curl"):
        pytest.skip("curl unavailable")
    import duckterm.core.orchestrator as orchestrator

    monkeypatch.setattr(orchestrator.tmux, "has_tmux", lambda: False)
    script = isolated / "agent.py"
    script.write_text(
        """import json, os, shlex, subprocess, sys, time
from pathlib import Path
settings = json.loads((Path(os.environ["CLAUDE_CONFIG_DIR"])/"settings.json").read_text())
command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
native = sys.argv[sys.argv.index("--session-id")+1]
payload = json.dumps({"session_id": native, "prompt": "test"})
subprocess.run(shlex.split(command), input=payload, text=True, check=True)
time.sleep(30)
"""
    )
    with closing(HistoryStore(isolated / "history.sqlite")) as history:
        server = Server(history=history)

        async def run():
            listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
            port = listener.sockets[0].getsockname()[1]
            key = None
            try:
                key = await server.orchestrator.launch(
                    runtime=ClaudeCodeRuntime(shlex.join([sys.executable, str(script)])),
                    cwd=str(isolated),
                    test=True,
                    env={
                        "CLAUDE_CONFIG_DIR": str(isolated / "config"),
                        "DUCKTERM_URL": f"http://127.0.0.1:{port}",
                        "DUCKTERM_PORT": str(port),
                    },
                )
                async with asyncio.timeout(8):
                    while hook_connection.status(history.session(key), 0)["state"] != "connected":
                        await asyncio.sleep(0.02)
                assert history.session(key)["state"] == "busy"
                assert (await server.conversation_recovery.identity(key))["hooks"]["connection"][
                    "state"
                ] == "connected"
            finally:
                if key:
                    await server.orchestrator.stop(key)
                listener.close()
                await listener.wait_closed()

        try:
            asyncio.run(run())
        finally:
            server.digests.close()
            history.purge_test_sessions()
