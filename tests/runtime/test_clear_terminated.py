"""Bulk clear of terminated sessions tears everything down — including tmux
panes for sessions the current server never supervised (launched before a
restart). Regression for the bug where clear-terminated dropped DB rows but
left the tmux sessions running forever.

Skipped when tmux isn't installed (no persistence path without it)."""

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest

from duckterm.agents import tmux
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server

_HAS_TMUX = shutil.which("tmux") is not None
FAKE_AGENT = Path(__file__).parent.parent / "fakes" / "fake_agent.py"


class _W:
    def __init__(self) -> None:
        self.data = b""

    def write(self, b: bytes) -> None:
        self.data += b

    async def drain(self) -> None:
        pass


def _body(w: _W) -> dict:
    return json.loads(w.data.split(b"\r\n\r\n", 1)[1])


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_clear_terminated_kills_unsupervised_tmux_session(tmp_path: Path) -> None:
    script = tmp_path / "s.txt"
    script.write_text("[busy]\n" * 100)
    cmd = f"{sys.executable} {FAKE_AGENT} --script {script} --delay 0.2"
    key = "clear-term-test"

    async def scenario() -> tuple[bool, dict, bool, bool]:
        store = HistoryStore(tmp_path / "db.sqlite")
        first = Server(history=store)
        await first.orchestrator.launch(
            runtime=GenericRuntime(cmd), cwd=str(tmp_path), session_key=key
        )
        await asyncio.sleep(0.5)
        alive_before = tmux.session_exists(tmux.target_for(key))

        # Simulate the real bug setup: the session ends up 'terminated' in the
        # DB while its tmux pane lives on, and a freshly restarted server (no
        # supervisor for it, no reconcile) handles the clear.
        store.set_state(key, "terminated", now=1)
        second = Server(history=store)
        w = _W()
        await second._clear_terminated(w)  # type: ignore[arg-type]
        alive_after = tmux.session_exists(tmux.target_for(key))
        return alive_before, _body(w), alive_after, store.session(key) is None

    try:
        alive_before, body, alive_after, row_gone = asyncio.run(scenario())
        assert alive_before is True
        assert body["session_keys"] == [key]
        assert body["skipped"] == []
        assert alive_after is False  # the pane died with the row
        assert row_gone is True
    finally:
        tmux.kill_session(tmux.target_for(key))
