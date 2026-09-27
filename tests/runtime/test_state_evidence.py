"""Ordinary output is not an agent state transition, on either transport."""

import asyncio
import shlex
import sys
from pathlib import Path

import pytest

from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.runtimes.codex import CodexRuntime
from duckterm.runtimes.copilot import CopilotRuntime
from duckterm.runtimes.generic import GenericRuntime


@pytest.mark.parametrize("force_pty", [False, True], ids=["tmux", "pty"])
@pytest.mark.parametrize(
    "runtime_type,marker,expected",
    [
        (CodexRuntime, "Working (2s • esc to interrupt)", []),
        (CopilotRuntime, "Thinking...", []),
        (ClaudeCodeRuntime, "❯ 1. Yes", ["Notification"]),
        (GenericRuntime, "[idle]", ["Stop"]),
        (GenericRuntime, "[waiting]", ["Notification"]),
    ],
)
def test_noise_does_not_replace_last_state(
    tmp_path: Path, monkeypatch, force_pty, runtime_type, marker, expected
) -> None:  # type: ignore[no-untyped-def]
    import duckterm.core.orchestrator as module

    if force_pty:
        monkeypatch.setattr(module.tmux, "has_tmux", lambda: False)
    elif not module.tmux.has_tmux():
        pytest.skip("tmux unavailable")
    script = tmp_path / "probe.py"
    script.write_text(
        "import time\ntime.sleep(.3)\n"
        f"print({marker!r}, flush=True)\ntime.sleep(.1)\n"
        "print('result: 42', flush=True)\ntime.sleep(.1)\n"
        "print('\\x1b[0m', flush=True)\ntime.sleep(.1)\n"
    )
    captured = []
    bus = EventBus(sink=captured.append)

    async def run() -> None:
        orch = Orchestrator(bus)
        key = await orch.launch(
            runtime=runtime_type(shlex.join([sys.executable, str(script)])),
            cwd=str(tmp_path),
            test=True,
        )
        sup = orch.get(key)
        assert sup is not None
        try:
            await asyncio.wait_for(sup._task, 5)  # type: ignore[arg-type]
            output = "".join(sup.output_tail())
            assert marker in output
            assert "result: 42" in output
        finally:
            await orch.stop(key)

    asyncio.run(run())
    states = [e["event_type"] for e in captured]
    assert states[0] == "SessionStart"
    assert states[-1] == "SessionEnd"
    assert states[1:-1] == expected
