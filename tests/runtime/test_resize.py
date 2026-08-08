"""SessionSupervisor.resize() on both backings. This is exactly where the
recently-fixed "garbled TUI — make tmux resize from the browser stick" bug
lived, and it had no test; a regression silently re-garbles every browser
terminal because the agent's TUI never reflows to the pane."""

import asyncio

import pytest

import duckterm.core.orchestrator as orch_mod
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.runtimes.generic import GenericRuntime


def test_pty_resize_sets_the_window_size_the_agent_sees(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PTY path (TIOCSWINSZ): after resize, the child's tty reports the new
    dimensions. We prove it end to end by having the agent print `stty size`
    (which reads rows cols from its controlling terminal) after the resize."""
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: False)
    orch = Orchestrator(EventBus())

    async def scenario() -> str:
        # Wait a beat so resize() lands before stty reads the size, then print it.
        key = await orch.launch(
            runtime=GenericRuntime("sh -c 'sleep 0.4; stty size; sleep 2'"),
            cwd=str(tmp_path),
            session_key="rs",
        )
        sup = orch.get(key)
        assert sup is not None
        # Give the child a moment to open its PTY, then resize to a distinctive size.
        for _ in range(50):
            if sup.running and sup._primary_fd is not None:
                break
            await asyncio.sleep(0.02)
        assert sup.resize(133, 47) is True  # cols=133, rows=47
        out = ""
        for _ in range(120):  # up to ~6s for stty to print
            out = "".join(sup.output_tail())
            if "47 133" in out:  # stty size prints "rows cols"
                break
            await asyncio.sleep(0.05)
        await orch.stop(key)
        return out

    out = asyncio.run(scenario())
    assert "47 133" in out  # the agent's tty saw the resize


def test_tmux_resize_passes_cols_and_rows_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """tmux path: resize() forwards cols/rows to tmux.resize_window for the
    session's target (the bug was these not sticking). Assert the call, without
    spawning real tmux."""
    from duckterm.core.orchestrator import SessionSupervisor

    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(
        orch_mod.tmux,
        "resize_window",
        lambda target, cols, rows: calls.append((target, cols, rows)) or True,
    )

    sup = SessionSupervisor(
        bus=EventBus(), runtime=GenericRuntime("true"), session_key="t", cwd="/tmp"
    )
    sup._tmux_target = "rd_t"
    # `running` is true while the tail task is alive; fake it for a unit check.
    monkeypatch.setattr(type(sup), "running", property(lambda self: True))

    assert sup.resize(120, 40) is True
    assert calls == [("rd_t", 120, 40)]


def test_resize_is_a_noop_when_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    from duckterm.core.orchestrator import SessionSupervisor

    sup = SessionSupervisor(
        bus=EventBus(), runtime=GenericRuntime("true"), session_key="d", cwd="/tmp"
    )
    # No tmux target, no PTY fd, not running → nothing to resize.
    assert sup.resize(100, 30) is False
