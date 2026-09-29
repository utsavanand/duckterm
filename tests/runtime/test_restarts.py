"""Restart must never stop the wrong conversation or discard an unsent draft."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.runtime.test_codex_resume_identity import A, seed
from tests.runtime.test_codex_resume_identity import conversations as make_conversations
from tests.runtime.test_session_api import Writer, dispatch

from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.restarts import Restarts
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.runtimes.codex import CodexRuntime
from duckterm.server import Server


@pytest.fixture
def rig(tmp_path, monkeypatch):
    conversations = make_conversations.__wrapped__(tmp_path, monkeypatch)
    history = HistoryStore(tmp_path / "restart.sqlite")
    seed(history, conversations, "a", A)
    history.set_state("a", "busy", now=int(time.time() * 1000) - 100)
    server = Server(history=history)
    screen = ["────────────────\n› \n────────────────"]
    sup = SimpleNamespace(running=True, last_owner_input_ms=0, visible_screen=lambda: screen[0])
    monkeypatch.setattr(server.orchestrator, "get", lambda key: sup if key == "a" else None)
    monkeypatch.setattr("duckterm.restarts.shutil.which", lambda _: "/test/codex")
    calls = []

    async def stop(key):
        calls.append(("stop", key))
        return True

    async def launch(**kwargs):
        calls.append(
            (
                "launch",
                kwargs["session_key"],
                kwargs["runtime"].launch_command(
                    cwd=Path(kwargs["cwd"]), session_key=kwargs["session_key"], initial_prompt=""
                ),
            )
        )
        server.bus.publish({"event_type": "SessionStart", "session_key": "a", "test": True})
        return "a"

    versions = iter(["codex 1.0", "codex 2.0"])

    async def version(_):
        return next(versions)

    monkeypatch.setattr(server.orchestrator, "stop", stop)
    monkeypatch.setattr(server.orchestrator, "launch", launch)
    monkeypatch.setattr("duckterm.restarts.cli_version", version)
    yield server, sup, screen, calls
    history.close()


async def hook(server, **overrides):
    await asyncio.sleep(0.003)  # real ordering beyond the launch event's ms tick
    await server._ingest(
        Writer(),
        json.dumps(
            {
                "event_type": "Stop",
                "session_key": "a",
                "session_id": A,
                "stop_hook_active": False,
                "test": True,
                **overrides,
            }
        ).encode(),
    )


async def drain(server):
    tasks = list(server.restarts.tasks.values())
    if tasks:
        await asyncio.gather(*tasks)


def test_busy_waits_for_real_hook_then_same_key_model_version_and_resume(rig):
    server, _, _, calls = rig

    async def run():
        server.restarts.save("a", cli_version="codex 0.9")
        result = await server.restarts.request("a", "new-model")
        assert result["status"] == "queued" and not calls
        # Output-derived idle is not a turn end, including after a server restart.
        server.bus.publish({"event_type": "Stop", "session_key": "a"})
        server.restarts = Restarts(server)
        assert server.restarts.read("a")["status"] == "queued"
        assert not server.restarts.turn_finished("a")
        await hook(server)
        await drain(server)
        assert calls == [
            ("stop", "a"),
            ("launch", "a", ["codex", "-c", 'model="new-model"', "resume", A]),
        ]
        state = server.restarts.read("a")
        assert state["status"] == "completed", state
        assert state["cli_version"] == "codex 2.0"
        assert state["previous_cli_version"] == "codex 0.9"
        assert server._resume_argv("a", "codex", server.history.session("a"))[0] == calls[1][2]

    asyncio.run(run())


def test_canceled_queue_cannot_restart_and_model_is_not_changed(rig):
    server, _, _, calls = rig

    async def run():
        await server.restarts.request("a", "new-model")
        server.restarts.cancel("a")
        await hook(server)
        await drain(server)
        assert not calls
        assert not server.restarts.read("a").get("configured_model")

    asyncio.run(run())


def test_draft_at_request_or_execution_never_stops(rig, monkeypatch):
    server, _, screen, calls = rig

    async def run():
        screen[0] = "────────────────\n› unsent draft\n────────────────"
        with pytest.raises(APIError, match="unsent"):
            await server.restarts.request("a", "new-model")
        screen[0] = "────────────────\n› \n────────────────"
        await server.restarts.request("a", "new-model")
        screen[0] = "────────────────\n› typed while waiting\n────────────────"
        await hook(server)
        await drain(server)
        assert not calls
        assert server.restarts.read("a")["status"] == "failed"
        assert "preserved" in server.restarts.read("a")["error"]

    asyncio.run(run())


def test_new_turn_during_version_probe_defers_restart(rig, monkeypatch):
    server, _, _, calls = rig

    async def version(_):
        await hook(server, event_type="UserPromptSubmit")
        return "codex 2"

    monkeypatch.setattr("duckterm.restarts.cli_version", version)

    async def run():
        await server.restarts.request("a", "new-model")
        await hook(server)
        await drain(server)
        assert not calls
        assert server.restarts.read("a")["status"] == "queued"

    asyncio.run(run())


@pytest.mark.parametrize(
    "fields", [{"stop_hook_active": True}, {"agent_id": "child"}, {"session_id": "different"}]
)
def test_subagent_recursive_or_wrong_identity_stop_cannot_release_queue(rig, fields):
    server, _, _, calls = rig

    async def run():
        await server.restarts.request("a", "new-model")
        await hook(server, **fields)
        await drain(server)
        assert not calls
        assert server.restarts.read("a")["status"] == "queued"

    asyncio.run(run())


def test_exact_identity_missing_refuses_before_stopping(rig, monkeypatch):
    server, _, _, calls = rig
    monkeypatch.setattr(server.history, "session_id_for", lambda key: None)

    async def run():
        with pytest.raises(APIError, match="Cannot verify"):
            await server.restarts.request("a", "new-model")
        assert not calls
        assert (await server.restarts.describe("a"))["can_restart"] is False

    asyncio.run(run())


def test_failure_after_stop_is_visible_and_crash_does_not_replay(rig, monkeypatch):
    server, _, _, calls = rig

    async def fail(**kwargs):
        raise OSError("binary disappeared")

    monkeypatch.setattr(server.orchestrator, "launch", fail)

    async def run():
        server.restarts.save("a", configured_model="working-model")
        await server.restarts.request("a", "new-model")
        await hook(server)
        await drain(server)
        assert calls == [("stop", "a")]
        assert server.restarts.read("a")["status"] == "failed"
        assert "binary disappeared" in server.restarts.read("a")["error"]
        assert server.restarts.read("a")["configured_model"] == "working-model"
        assert server._resume_argv("a", "codex", server.history.session("a"))[0] == [
            "codex",
            "-c",
            'model="working-model"',
            "resume",
            A,
        ]
        server.restarts.save("a", status="restarting")
        server.restarts = Restarts(server)
        assert server.restarts.read("a")["status"] == "failed"
        assert not server.restarts.tasks

    asyncio.run(run())


def test_queue_survives_database_reopen_and_controls_are_owner_authenticated(rig, tmp_path):
    server, _, _, _ = rig
    server.restarts.save("a", status="queued", requested_model="later")
    # A separate connection sees committed control state, independent of an app or manager.
    with_db = HistoryStore(tmp_path / "restart.sqlite")
    assert with_db.restart_control("a")["requested_model"] == "later"
    with_db.close()
    status, _ = dispatch(server, "POST", "/sessions/a/restart", {}, b'{"model":"x"}')
    assert status == 401
    status, data = dispatch(
        server, "DELETE", "/sessions/a/restart", {"x-duckterm-token": server.token}
    )
    assert status == 200 and data["status"] == "canceled"


def test_model_flags_keep_values_as_arguments():
    assert ClaudeCodeRuntime().model_arguments("my model") == ["--model", "my model"]
    assert CodexRuntime().model_arguments('model"quoted') == ["-c", 'model="model\\"quoted"']


def test_restart_liveness_checks_leave_the_loop_free_to_drain_terminal_output(rig, monkeypatch):
    import threading

    server, original, _, calls = rig

    async def run():
        loop = asyncio.get_running_loop()

        class OutputDependentTerminal:
            last_owner_input_ms = 0
            visible_screen = staticmethod(original.visible_screen)

            @property
            def running(self):
                drained = threading.Event()
                loop.call_soon_threadsafe(drained.set)
                assert drained.wait(1), "liveness probe blocked the output-draining loop"
                return True

        terminal = OutputDependentTerminal()
        monkeypatch.setattr(
            server.orchestrator, "get", lambda key: terminal if key == "a" else None
        )
        assert (await server.restarts.describe("a"))["can_restart"]
        await server.restarts.request("a", "new-model")
        await hook(server)
        await drain(server)
        assert server.restarts.read("a")["status"] == "completed"
        assert len(calls) == 2

    asyncio.run(run())


def test_restart_blocks_transfer_and_bulk_delete_during_relaunch(rig):
    from duckterm.transfer_api import source_session

    server, _, _, calls = rig
    server.restarts.save("a", status="restarting")
    server.history.set_state("a", "terminated", now=int(time.time() * 1000))
    with pytest.raises(ValueError, match="restart"):
        asyncio.run(source_session(server, "a"))
    status, data = dispatch(
        server, "POST", "/sessions/clear-terminated", {"x-duckterm-token": server.token}
    )
    assert status == 200 and data["skipped"] == ["a"]
    assert server.history.session("a") and not calls


def test_turn_finishing_during_prior_probe_is_not_lost(rig, monkeypatch):
    server, _, _, calls = rig
    probes = 0

    async def version(_):
        nonlocal probes
        probes += 1
        if probes == 1:
            await hook(server, event_type="UserPromptSubmit")
            await hook(server)
        return "codex 2"

    monkeypatch.setattr("duckterm.restarts.cli_version", version)

    async def run():
        await server.restarts.request("a", "new-model")
        await hook(server)
        while server.restarts.tasks:
            await drain(server)
        assert len(calls) == 2
        assert server.restarts.read("a")["status"] == "completed"

    asyncio.run(run())


def test_real_isolated_terminal_restarts_under_same_key(tmp_path, monkeypatch):
    """Exercise the real stop/resume lifecycle using only our fake CLI and tmux socket."""
    import os
    import shlex
    import uuid

    repo = make_conversations.__wrapped__(tmp_path, monkeypatch)
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    version_file = tmp_path / "cli-version"
    version_file.write_text("fake-codex 1.0")
    binary = binary_dir / "codex"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import os,sys,time\n"
        f"version_file={str(version_file)!r}\n"
        "if '--version' in sys.argv:\n"
        " print(open(version_file).read());sys.exit(0)\n"
        "os.write(1,b'\\x1b[2J\\x1b[HFAKE_RESTART_CLI\\r\\n')\n"
        "os.write(1,'────────────────\\r\\n› \\r\\n────────────────\\r\\n'.encode())\n"
        "while True: time.sleep(1)\n"
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ["PATH"])
    socket = "f15-test-" + uuid.uuid4().hex[:10]
    monkeypatch.setenv("DUCKTERM_TMUX_SOCKET", socket)
    store = HistoryStore(tmp_path / "real.sqlite")
    server = Server(history=store)
    monkeypatch.setattr(server, "_maybe_refresh_progress", lambda key: None)
    monkeypatch.setattr(server, "_relay_observe", lambda event: None)

    async def run():
        try:
            await server.orchestrator.launch(
                runtime=CodexRuntime(), cwd=str(repo), session_key="a", test=True
            )
            # Bind the synthetic exact rollout to the synthetic launched row.
            await hook(server, event_type="SessionStart")
            first = server.orchestrator.get("a")
            for _ in range(50):
                if await server.restarts.draft_free(first, CodexRuntime()):
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("fake CLI did not paint its empty prompt")
            version_file.write_text("fake-codex 2.0")
            await server.restarts.request("a", "test-model")
            assert server.restarts.read("a")["status"] == "queued"
            await hook(server)
            await drain(server)
            state = server.restarts.read("a")
            assert state["status"] == "completed", state
            assert state["cli_version"] == "fake-codex 2.0"
            second = server.orchestrator.get("a")
            assert second is not first and second.running
            row = store.session("a")
            command = shlex.split(row["command"])
            assert command[:5] == ["codex", "-c", 'model="test-model"', "resume", A]
            assert row["test"] == 1
        finally:
            await server.restarts.close()
            await server.orchestrator.stop("a")
            if store.session("a"):
                store.delete_session("a")

    try:
        asyncio.run(run())
    finally:
        # Only the namespace created above, never the production duckterm socket.
        import subprocess

        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True)
        store.close()
