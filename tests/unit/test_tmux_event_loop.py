"""A tmux response can depend on the event loop draining its output client."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from duckterm import transfer_api
from duckterm.agents import tmux
from duckterm.core import events
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import SessionSupervisor
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server


def supervisor(tmp_path):
    sup = SessionSupervisor(
        bus=EventBus(),
        runtime=GenericRuntime("cat"),
        session_key="test-loop",
        cwd=str(tmp_path),
        extra={"test": True},
    )
    sup._tmux_target = "rd_test-loop"
    sup._pipe_path = str(tmp_path / "pane.log")
    (tmp_path / "pane.log").write_bytes(b"")
    return sup


def dependent_response(loop, value):
    """Model tmux waiting for work on the loop; watchdog only bounds a failure."""
    observations = []

    def response(*args, **kwargs):
        drained = threading.Event()
        loop.call_soon_threadsafe(drained.set)
        observations.append(drained.wait(2))
        return value

    return response, observations


def test_slow_liveness_does_not_end_tail_or_block_output(tmp_path, monkeypatch):
    async def scenario():
        sup = supervisor(tmp_path)
        emitted = []
        monkeypatch.setattr(sup, "_emit", lambda event, **kw: emitted.append(event))
        loop = asyncio.get_running_loop()
        probes = []

        def exists(target):
            drained = threading.Event()

            def consume_output():
                assert events.SESSION_END not in emitted
                if not probes:
                    with open(sup._pipe_path, "ab") as output:
                        output.write(b"still alive\r\n")
                drained.set()

            loop.call_soon_threadsafe(consume_output)
            probes.append(drained.wait(2))
            return len(probes) == 1

        monkeypatch.setattr(tmux, "session_exists", exists)
        await sup._tail_pipe()
        assert probes == [True, True]
        assert b"still alive\r\n" in b"".join(sup._byte_tail)
        assert emitted.count(events.SESSION_END) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["resize", "input", "screen", "stop", "transfer"])
def test_request_tmux_wait_leaves_event_loop_available(tmp_path, monkeypatch, operation):
    async def scenario():
        sup = supervisor(tmp_path)
        response, observations = dependent_response(asyncio.get_running_loop(), True)
        monkeypatch.setattr(tmux, "session_exists", response)
        monkeypatch.setattr(tmux, "resize_window", response)
        monkeypatch.setattr(tmux, "send_keys", response)
        monkeypatch.setattr(tmux, "kill_session", response)
        capture, captures = dependent_response(asyncio.get_running_loop(), b"working")
        monkeypatch.setattr(tmux, "capture_screen", capture)
        server = SimpleNamespace(orchestrator=SimpleNamespace(get=lambda key: sup))
        if operation == "resize":
            await Server._handle_terminal_frame(sup, (1, b'{"resize":{"cols":100,"rows":30}}'))
        elif operation == "input":

            async def write_json(*args):
                pass

            monkeypatch.setattr("duckterm.server._write_json", write_json)
            await Server._input(server, None, "test-loop", b'{"text":"hello"}')
        elif operation == "screen":
            await Server._reconcile_waiting(
                server,
                {
                    "session_key": "test-loop",
                    "state": "waiting",
                    # Only an auto-reviewing agent's wait is ever read from the screen.
                    "runtime": "codex",
                },
            )
            assert captures == [True]
        elif operation == "stop":
            await sup.stop()
        else:
            server.history = SimpleNamespace(session=lambda key: {"state": "stopped"})
            server.restarts = SimpleNamespace(running=lambda key: False)
            with pytest.raises(ValueError, match="still running"):
                await transfer_api.source_session(server, "test-loop")
        assert observations and all(observations)

    asyncio.run(scenario())


def test_approval_callback_queues_keys_in_order(tmp_path, monkeypatch):
    from duckterm.core.orchestrator import Orchestrator

    async def scenario():
        sup = supervisor(tmp_path)
        orch = Orchestrator(EventBus())
        orch._supervisors[sup.session_key] = sup
        sent = []
        response, observations = dependent_response(asyncio.get_running_loop(), True)

        def write(data):
            response()
            sent.append(data)
            return True

        monkeypatch.setattr(sup, "write_bytes", write)
        try:
            assert orch.inject_key(sup.session_key, "1")
            assert orch.inject_key(sup.session_key, "Escape")
            assert orch.inject_key(sup.session_key, "Enter")
            async with asyncio.timeout(5):
                while len(b"".join(sent)) < 4:
                    await asyncio.sleep(0.01)
            assert b"".join(sent) == b"1\r\x1b\r"
            assert observations and all(observations)
        finally:
            if sup._input_task:
                sup._input_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await sup._input_task

    asyncio.run(scenario())
