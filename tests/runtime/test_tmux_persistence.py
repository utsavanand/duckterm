"""tmux-backed sessions survive a server restart: a session launched by one
Orchestrator is re-adopted by a fresh one and remains controllable.

Skipped when tmux isn't installed (the PTY path has no persistence)."""

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

from duckterm.agents import tmux
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime

_HAS_TMUX = shutil.which("tmux") is not None
FAKE_AGENT = Path(__file__).parent.parent / "fakes" / "fake_agent.py"


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_session_is_readopted_after_restart(tmp_path: Path) -> None:
    # A long-lived agent so it's still running when the second orchestrator starts.
    script = tmp_path / "s.txt"
    script.write_text("[busy]\n" * 100)
    cmd = f"{sys.executable} {FAKE_AGENT} --script {script} --delay 0.2"
    key = "persist-test"

    async def scenario() -> tuple[bool, list[str], bool]:
        store = HistoryStore(tmp_path / "db.sqlite")
        bus1 = EventBus(sink=lambda event: store.record({**event, "test": True}))
        orch1 = Orchestrator(bus1, history=store)
        await orch1.launch(runtime=GenericRuntime(cmd), cwd=str(tmp_path), session_key=key)
        await asyncio.sleep(0.5)
        alive_after_launch = tmux.session_exists(tmux.target_for(key))
        store.set_state(key, "interrupted", now=3)

        # Simulate a restart: a brand-new orchestrator over the same tmux/home.
        bus2 = EventBus(sink=lambda event: store.record({**event, "test": True}))
        orch2 = Orchestrator(bus2, history=store)
        adopted = await orch2.reconcile()
        assert store.session(key)["state"] != "interrupted"
        assert store.session(key)["ended_at"] is None
        # The re-adopted supervisor can control the session.
        stopped = await orch2.stop(key)
        return alive_after_launch, adopted, stopped

    try:
        alive, adopted, stopped = asyncio.run(scenario())
        assert alive is True
        assert key in adopted  # the fresh orchestrator re-adopted it
        assert stopped is True  # and could control it
    finally:
        tmux.kill_session(tmux.target_for(key))


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_reader_shutdown_preserves_status_and_real_exit_still_completes(tmp_path: Path):
    import contextlib
    import shlex

    key = "reader-shutdown-test"
    command = shlex.join([sys.executable, "-c", "import time; time.sleep(60)"])
    store = HistoryStore(tmp_path / "db.sqlite")
    completed = []

    async def scenario():
        bus = EventBus(sink=lambda event: store.record({**event, "test": True}))
        first = Orchestrator(bus, history=store)
        first.on_completed = completed.append
        await first.launch(
            runtime=GenericRuntime(command), cwd=str(tmp_path), session_key=key, test=True
        )
        reader = first.get(key)
        await asyncio.sleep(0.1)
        before = store.session(key)
        assert reader._task is not None
        reader._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader._task
        await asyncio.sleep(0)
        assert completed == []
        assert tmux.session_exists(tmux.target_for(key))
        after = store.session(key)
        assert after["state"] == before["state"]
        assert after["ended_at"] is None
        assert after["event_count"] == before["event_count"]

        second = Orchestrator(bus, history=store)
        assert key in await second.reconcile()
        assert store.session(key)["state"] == before["state"]
        await second.stop(key)
        assert store.session(key)["state"] == "terminated"
        assert store.session(key)["ended_at"] is not None

    try:
        asyncio.run(scenario())
    finally:
        tmux.kill_session(tmux.target_for(key))
        store.close()
