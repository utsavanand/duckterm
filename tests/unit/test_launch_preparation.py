import asyncio

import pytest

from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import SessionSupervisor
from duckterm.runtimes.generic import GenericRuntime


def test_all_terminal_input_paths_reject_before_native_preparation_and_allow_after(
    monkeypatch, tmp_path
):
    reached = asyncio.Event()
    release = asyncio.Event()
    writes = []

    class Preparation:
        argv = ["/bin/cat"]
        env = {}
        allow_automation = True
        closed = False

        async def ready(self, pid, owns_process, on_status=None):
            assert pid == 123 and owns_process()
            reached.set()
            await release.wait()

        async def close(self):
            self.closed = True

    prepared = Preparation()

    class Runtime(GenericRuntime):
        requires_launch_preparation = True

        async def prepare_launch(self, **kwargs):
            return prepared

    supervisor = SessionSupervisor(
        bus=EventBus(sink=lambda event: None),
        runtime=Runtime("/bin/cat"),
        session_key="test-native-preparation",
        cwd=str(tmp_path),
        extra={"test": True},
    )

    async def spawned(argv):
        assert argv == ["/bin/cat"]
        supervisor._tmux_target = "fixture-only"

    monkeypatch.setattr(supervisor, "_start_tmux", spawned)
    monkeypatch.setattr("duckterm.agents.tmux.has_tmux", lambda: True)
    monkeypatch.setattr("duckterm.agents.tmux.pane_pid", lambda target: 123)
    monkeypatch.setattr("duckterm.agents.tmux.session_exists", lambda target: True)
    monkeypatch.setattr(
        "duckterm.agents.tmux.send_raw", lambda target, data: writes.append(data) or True
    )
    monkeypatch.setattr(
        "duckterm.agents.tmux.send_keys", lambda target, data, **kw: writes.append(data) or True
    )
    monkeypatch.setattr("duckterm.agents.tmux.kill_session", lambda target: True)

    async def run():
        task = asyncio.create_task(supervisor.start())
        await reached.wait()
        assert not supervisor.write_bytes(b"early bytes")
        assert not supervisor.write_input("early command\n")
        assert not await supervisor.write_queued_bytes(b"early feedback")
        supervisor.queue_bytes(b"early queue")
        assert writes == [] and supervisor._input_queue is None
        release.set()
        await task
        assert supervisor.write_bytes(b"after bytes")
        assert supervisor.write_input("after command\n")
        assert await supervisor.write_queued_bytes(b"after feedback")
        assert writes == [b"after bytes", "after command", b"after feedback"]
        await supervisor.stop()
        assert prepared.closed and not supervisor.write_bytes(b"after stop")

    asyncio.run(run())


def test_required_preparation_cannot_fall_back_to_plain_launch(tmp_path):
    class Unprepared(GenericRuntime):
        requires_launch_preparation = True

    supervisor = SessionSupervisor(
        bus=EventBus(),
        runtime=Unprepared("/bin/cat"),
        session_key="test-unprepared",
        cwd=str(tmp_path),
        extra={"test": True},
    )
    with pytest.raises(ValueError, match="requires verified native preparation"):
        asyncio.run(supervisor.start())
    assert not supervisor.running
