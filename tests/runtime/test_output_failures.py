"""Failed output setup must report the loss and release unowned PTYs."""

import asyncio
import contextlib
import os
import pty

import pytest

from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import SessionSupervisor
from duckterm.runtimes.generic import GenericRuntime


@pytest.mark.parametrize("error", [PermissionError("denied"), asyncio.CancelledError()])
def test_spawn_failure_closes_both_unowned_descriptors(tmp_path, monkeypatch, error):
    descriptors = pty.openpty()
    monkeypatch.setattr("duckterm.core.orchestrator.pty.openpty", lambda: descriptors)

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    sup = SessionSupervisor(
        bus=EventBus(),
        runtime=GenericRuntime("unused"),
        session_key="test-failure",
        cwd=str(tmp_path),
        extra={"test": True},
    )
    try:
        with pytest.raises(type(error)):
            asyncio.run(sup._start_pty(["unused"]))
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        for fd in descriptors:
            with contextlib.suppress(OSError):
                os.close(fd)


def test_invalid_writer_marker_reports_capture_failure(tmp_path):
    bus = EventBus()
    sup = SessionSupervisor(
        bus=bus,
        runtime=GenericRuntime("unused"),
        session_key="test-marker",
        cwd=str(tmp_path),
        extra={"test": True},
    )
    sup._pipe_path = str(tmp_path / "output")
    sup._tmux_target = "never-probed"
    (tmp_path / "output.writer").write_text("invalid")
    asyncio.run(sup._tail_pipe())
    final = bus.recent()[-1]
    assert final["event_type"] == "SessionEnd"
    assert "Invalid pane writer generation" in final["output_error"]
