"""Tier 3 backend: a supervised agent's output is captured and retrievable, and
input can be written to a live agent's stdin (terminal-attach)."""

import asyncio
import shlex
import sys
from pathlib import Path

import pytest

from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.runtimes.generic import GenericRuntime

FAKE_AGENT = Path(__file__).parent.parent / "fakes" / "fake_agent.py"


def test_output_tail_captures_agent_output(tmp_path: Path) -> None:
    bus = EventBus()
    orch = Orchestrator(bus)
    script = tmp_path / "s.txt"
    script.write_text("[busy]\n[tool] build\n[idle]\n")
    cmd = f"{sys.executable} {FAKE_AGENT} --script {script}"

    async def scenario() -> list[str]:
        key = await orch.launch(runtime=GenericRuntime(cmd), cwd=str(tmp_path), test=True)
        sup = orch.get(key)
        assert sup is not None
        await asyncio.wait_for(sup._task, 5)  # type: ignore[arg-type]
        return sup.output_tail()

    output = asyncio.run(scenario())
    joined = "".join(output)
    assert "[busy]" in joined
    assert "[tool] build" in joined
    assert "[idle]" in joined


def test_write_input_returns_false_when_not_running(tmp_path: Path) -> None:
    bus = EventBus()
    orch = Orchestrator(bus)
    cmd = f"{sys.executable} {FAKE_AGENT}"

    async def scenario() -> bool:
        key = await orch.launch(runtime=GenericRuntime(cmd), cwd=str(tmp_path), test=True)
        sup = orch.get(key)
        assert sup is not None
        await asyncio.wait_for(sup._task, 5)  # type: ignore[arg-type]
        # Agent has exited; writing input must fail cleanly, not raise.
        return sup.write_input("hello\n")

    assert asyncio.run(scenario()) is False


def test_subscribe_output_replays_then_streams(tmp_path: Path) -> None:
    bus = EventBus()
    orch = Orchestrator(bus)
    script = tmp_path / "s.txt"
    script.write_text("[busy]\n[idle]\n")
    cmd = f"{sys.executable} {FAKE_AGENT} --script {script} --delay 0.05"

    async def scenario() -> list[str]:
        key = await orch.launch(runtime=GenericRuntime(cmd), cwd=str(tmp_path), test=True)
        sup = orch.get(key)
        assert sup is not None
        feed = sup.subscribe_output()
        collected: list[str] = []
        try:
            # Pull a couple of lines as they stream.
            for _ in range(2):
                collected.append(await asyncio.wait_for(feed.__anext__(), 5))
        finally:
            await feed.aclose()
            await asyncio.wait_for(sup._task, 5)  # type: ignore[arg-type]
        return collected

    lines = asyncio.run(scenario())
    assert any("[busy]" in line for line in lines)


def test_tail_drains_bytes_written_during_final_liveness_probe(tmp_path, monkeypatch):
    """The writer can flush between the empty read and the dead-pane result."""
    from duckterm.agents import tmux
    from duckterm.core.orchestrator import SessionSupervisor

    path = tmp_path / "pane.log"
    path.write_bytes(b"")
    events = []
    sup = SessionSupervisor(
        bus=EventBus(sink=events.append),
        runtime=GenericRuntime("true"),
        session_key="final-output",
        cwd=str(tmp_path),
        extra={"test": True},
    )
    sup._pipe_path = str(path)
    sup._tmux_target = "test-final-output"

    def dead(_):
        path.write_bytes(b"[busy]\r\nlast error without newline")
        return False

    monkeypatch.setattr(tmux, "session_exists", dead)
    asyncio.run(sup._tail_pipe())
    assert b"".join(sup._byte_tail) == b"[busy]\r\nlast error without newline"
    assert "last error without newline" in "".join(sup.output_tail())
    assert events[-1]["event_type"] == "SessionEnd"


@pytest.mark.parametrize("transport", ["pty", "tmux"])
def test_immediate_stdout_stderr_survive_delayed_reader(tmp_path, monkeypatch, transport):
    from duckterm.agents import tmux
    from duckterm.core.orchestrator import SessionSupervisor

    if transport == "tmux" and not tmux.has_tmux():
        pytest.skip("tmux unavailable")
    if transport == "pty":
        monkeypatch.setattr(tmux, "has_tmux", lambda: False)
        original = SessionSupervisor._pump

        async def after_exit(self, primary):
            assert self._proc is not None
            await self._proc.wait()  # deterministic: reader starts after the child exits
            await original(self, primary)

        monkeypatch.setattr(SessionSupervisor, "_pump", after_exit)
    command = shlex.join(
        [sys.executable, "-c", "import os;os.write(1,b'FINAL_STDOUT');os.write(2,b'FINAL_STDERR')"]
    )

    async def scenario():
        orch = Orchestrator(EventBus())
        key = await orch.launch(runtime=GenericRuntime(command), cwd=str(tmp_path), test=True)
        sup = orch.get(key)
        assert sup is not None
        try:
            await asyncio.wait_for(sup._task, 10)
            assert sup._primary_fd is None and sup._secondary_fd is None
            assert b"".join(sup._byte_tail) == b"FINAL_STDOUTFINAL_STDERR"
            assert "FINAL_STDOUTFINAL_STDERR" in "".join(sup.output_tail())
        finally:
            await orch.stop(key)

    asyncio.run(scenario())


def test_tail_waits_for_writer_eof_before_session_end(tmp_path, monkeypatch):
    from duckterm.agents import tmux
    from duckterm.core.orchestrator import SessionSupervisor
    from duckterm.helpers.pane_log import prepare_completion
    from duckterm.helpers.private_files import private_write

    path = tmp_path / "pane.log"
    path.write_bytes(b"")
    done = prepare_completion(path)
    observed_at_end = []
    sup = SessionSupervisor(
        bus=EventBus(
            sink=lambda event: (
                observed_at_end.append(b"".join(sup._byte_tail))
                if event["event_type"] == "SessionEnd"
                else None
            )
        ),
        runtime=GenericRuntime("true"),
        session_key="delayed-writer",
        cwd=str(tmp_path),
        extra={"test": True},
    )
    sup._pipe_path = str(path)
    sup._tmux_target = "test-delayed-writer"
    monkeypatch.setattr(tmux, "session_exists", lambda _: False)
    sleep = asyncio.sleep

    async def release_writer(_):
        path.write_bytes(b"last error")
        private_write(done, "complete")
        await sleep(0)

    monkeypatch.setattr(asyncio, "sleep", release_writer)
    asyncio.run(sup._tail_pipe())
    assert observed_at_end == [b"last error"]
    assert done.read_text() == "complete"  # another adopter may still need this EOF proof


def test_failed_capture_attach_never_releases_agent(tmp_path, monkeypatch):
    from duckterm.agents import tmux

    calls = []
    monkeypatch.setattr(tmux, "spawn", lambda *args: "rd_test-attach-failure")

    def command(*args):
        calls.append(args)
        return (False, "capture failed") if args[0] == "pipe-pane" else (True, "")

    monkeypatch.setattr(tmux, "_tmux", command)
    with pytest.raises(ValueError, match="Cannot attach terminal output"):
        tmux.spawn_piped("test-attach-failure", "true", str(tmp_path), str(tmp_path / "pane.log"))
    assert [call[0] for call in calls] == ["pipe-pane", "kill-session"]


def test_missing_writer_completion_reports_failure_without_hanging(tmp_path, monkeypatch):
    from duckterm.agents import tmux
    from duckterm.core.orchestrator import SessionSupervisor
    from duckterm.helpers.pane_log import prepare_completion

    path = tmp_path / "pane.log"
    path.write_bytes(b"partial error")
    prepare_completion(path)
    events = []
    sup = SessionSupervisor(
        bus=EventBus(sink=events.append),
        runtime=GenericRuntime("true"),
        session_key="missing-writer",
        cwd=str(tmp_path),
        extra={"test": True},
    )
    sup._pipe_path = str(path)
    sup._tmux_target = "test-missing-writer"
    monkeypatch.setattr(tmux, "session_exists", lambda _: False)
    monkeypatch.setattr("duckterm.core.orchestrator._TAIL_DRAIN_TIMEOUT", 0)
    asyncio.run(sup._tail_pipe())
    assert b"".join(sup._byte_tail) == b"partial error"
    assert events[-1]["event_type"] == "SessionEnd"
    assert "did not confirm EOF" in events[-1]["output_error"]
