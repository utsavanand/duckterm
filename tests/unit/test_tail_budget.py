"""Continuous terminal output must leave room for other sessions and requests."""

import asyncio
from pathlib import Path

from duckterm.agents import tmux
from duckterm.core import events
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import _TAIL_TICK_BYTES, SessionSupervisor
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.runtimes.generic import GenericRuntime


def supervisor(tmp_path, runtime=None):
    sup = SessionSupervisor(
        bus=EventBus(),
        runtime=runtime or GenericRuntime("cat"),
        session_key="test-budget",
        cwd=str(tmp_path),
        extra={"test": True},
    )
    sup._tmux_target = "rd_test-budget"
    sup._pipe_path = str(tmp_path / "pane.log")
    return sup


def test_large_output_yields_between_bounded_ticks_without_losing_bytes(tmp_path, monkeypatch):
    async def scenario():
        sup = supervisor(tmp_path)
        data = b"line\r\n" * (_TAIL_TICK_BYTES // 2)
        Path(sup._pipe_path).write_bytes(data)
        delivered = []
        sleeps = []
        monkeypatch.setattr(sup, "_record_bytes", delivered.append)
        monkeypatch.setattr(tmux, "session_exists", lambda target: False)
        original_sleep = asyncio.sleep

        async def sleep(delay):
            sleeps.append((delay, sum(map(len, delivered))))
            await original_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", sleep)
        await sup._tail_pipe()
        assert b"".join(delivered) == data
        assert sleeps[0] == (0.025, _TAIL_TICK_BYTES)
        assert len(sleeps) >= 2
        assert all(
            b[1] - a[1] <= _TAIL_TICK_BYTES for a, b in zip(sleeps, sleeps[1:], strict=False)
        )

    asyncio.run(scenario())


def test_liveness_is_not_spawned_on_every_empty_poll(tmp_path, monkeypatch):
    async def scenario():
        sup = supervisor(tmp_path)
        Path(sup._pipe_path).write_bytes(b"")
        loop = asyncio.get_running_loop()
        clock = [100.0]
        monkeypatch.setattr(loop, "time", lambda: clock[0])
        sup._last_input = clock[0]
        probes = []
        sleeps = []

        def exists(target):
            probes.append(clock[0])
            return len(probes) < 3

        original_sleep = asyncio.sleep

        async def sleep(delay):
            sleeps.append(delay)
            clock[0] += delay
            await original_sleep(0)

        monkeypatch.setattr(tmux, "session_exists", exists)
        monkeypatch.setattr(asyncio, "sleep", sleep)
        await sup._tail_pipe()
        assert len(sleeps) >= 80
        assert len(probes) == 3
        assert all(b - a >= 1.0 for a, b in zip(probes, probes[1:], strict=False))

    asyncio.run(scenario())


def test_rotation_follows_new_inode_and_keeps_raw_byte_order(tmp_path, monkeypatch):
    async def scenario():
        sup = supervisor(tmp_path)
        path = Path(sup._pipe_path)
        path.write_bytes(b"old\r\n")
        probes = []

        def exists(target):
            probes.append(target)
            if len(probes) == 1:
                path.rename(tmp_path / "previous.log")
                path.write_bytes(b"new\r\n")
                return True
            return False

        monkeypatch.setattr(tmux, "session_exists", exists)
        await sup._tail_pipe()
        assert b"".join(sup._byte_tail) == b"old\r\nnew\r\n"

    asyncio.run(scenario())


def test_hook_screen_scans_are_bounded_but_last_prompt_is_not_lost(tmp_path, monkeypatch):
    sup = supervisor(tmp_path, ClaudeCodeRuntime())
    scans = []
    monkeypatch.setattr(sup.runtime, "detect_state", lambda output: scans.append(output))
    for tick in range(100):
        for _ in range(200):
            sup._observe_output("spinner repaint\n")
        sup._scan_pending_output(tick * 0.025)
    assert len(scans) <= 10
    assert all(len(screen) <= 32 * 2048 for screen in scans)
    sup._observe_output("❯ final waiting prompt\n")
    sup._scan_pending_output(2.75)
    assert "final waiting prompt" in scans[-1]
    assert len(sup._output) == 2000


def test_generic_protocol_preserves_each_tool_and_state(tmp_path, monkeypatch):
    sup = supervisor(tmp_path)
    emitted = []
    monkeypatch.setattr(sup, "_emit", lambda event, **fields: emitted.append((event, fields)))
    for line in ["[tool] Read\n", "[idle]\n", "[waiting] question\n", "[busy]\n"]:
        sup._observe_output(line)
    assert emitted == [
        (events.PRE_TOOL_USE, {"tool_name": "Read"}),
        (events.STOP, {}),
        (events.NOTIFICATION, {}),
        (events.PRE_TOOL_USE, {}),
    ]


def test_pty_pending_prompt_is_scanned_without_more_output(tmp_path, monkeypatch):
    import os

    async def scenario():
        sup = supervisor(tmp_path, ClaudeCodeRuntime())
        read_fd, write_fd = os.pipe()

        async def finish():
            pass

        monkeypatch.setattr(sup, "_finish", finish)
        task = asyncio.create_task(sup._pump(read_fd))
        try:
            os.write(write_fd, b"initial output\n")
            await asyncio.sleep(0.03)
            os.write(write_fd, "❯ waiting for input\n".encode())
            async with asyncio.timeout(1):
                while sup._state != "waiting":
                    await asyncio.sleep(0.01)
            assert b"".join(sup._byte_tail) == "initial output\n❯ waiting for input\n".encode()
        finally:
            os.close(write_fd)
            await task

    asyncio.run(scenario())
