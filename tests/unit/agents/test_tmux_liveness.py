"""Passive batching must not weaken fresh actions or misclassify new panes."""

import asyncio
import threading
from unittest.mock import Mock

import pytest

from duckterm.agents import tmux
from duckterm.agents.tmux_liveness import TailLiveness
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import SessionSupervisor
from duckterm.runtimes.generic import GenericRuntime


def test_concurrent_tails_share_one_probe_and_refresh_after_expiry(monkeypatch):
    async def scenario():
        names = {f"rd_test-{i}" for i in range(39)}
        query = Mock(return_value=names)
        monkeypatch.setattr(tmux, "session_targets", query)
        monkeypatch.setattr(tmux, "session_exists", Mock(side_effect=AssertionError))
        monitor = TailLiveness()
        assert all(await asyncio.gather(*(monitor.exists(name) for name in names)))
        assert query.call_count == 1
        assert all(await asyncio.gather(*(monitor.exists(name) for name in names)))
        assert query.call_count == 1
        monitor._expires = 0
        assert all(await asyncio.gather(*(monitor.exists(name) for name in names)))
        assert query.call_count == 2

    asyncio.run(scenario())


def test_missing_name_is_checked_fresh_and_actions_ignore_snapshot(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(tmux, "session_targets", lambda: {"rd_old"})
        fresh = Mock(return_value=True)
        monkeypatch.setattr(tmux, "session_exists", fresh)
        monitor = TailLiveness()
        assert await monitor.exists("rd_old")
        assert await monitor.exists("rd_new")
        fresh.assert_called_once_with("rd_new")
        fresh.return_value = False
        assert not await monitor.exists("rd_new")
        sup = SessionSupervisor(
            bus=EventBus(),
            runtime=GenericRuntime("cat"),
            session_key="test-old",
            cwd=str(tmp_path),
            extra={"test": True},
            tail_liveness=monitor,
        )
        sup._tmux_target = "rd_old"
        assert await monitor.exists("rd_old")
        assert not sup.running
        fresh.assert_called_with("rd_old")

    asyncio.run(scenario())


def test_discovery_failure_stays_unknown_then_recovers(monkeypatch, capsys):
    async def scenario():
        query = Mock(side_effect=[RuntimeError("socket unavailable"), set()])
        monkeypatch.setattr(tmux, "session_targets", query)
        fresh = Mock(return_value=False)
        monkeypatch.setattr(tmux, "session_exists", fresh)
        monitor = TailLiveness()
        assert all(await asyncio.gather(*(monitor.exists(f"rd_test-{i}") for i in range(39))))
        assert query.call_count == 1
        fresh.assert_not_called()
        monitor._expires = 0
        assert not await monitor.exists("rd_test-0")
        fresh.assert_called_once_with("rd_test-0")

    asyncio.run(scenario())
    assert "tail liveness unavailable" in capsys.readouterr().err


def test_cancelling_one_waiter_does_not_cancel_shared_probe(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()

        def query():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(3), "event loop blocked while tmux awaited output"
            return {"rd_first", "rd_second"}

        probe = Mock(side_effect=query)
        monkeypatch.setattr(tmux, "session_targets", probe)
        monitor = TailLiveness()
        first = asyncio.create_task(monitor.exists("rd_first"))
        second = asyncio.create_task(monitor.exists("rd_second"))
        try:
            await started.wait()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        finally:
            release.set()
        assert await second
        assert probe.call_count == 1

    asyncio.run(scenario())


def test_socket_change_does_not_reuse_previous_snapshot(monkeypatch):
    async def scenario():
        monkeypatch.setenv("DUCKTERM_TMUX_SOCKET", "test-first")
        probe = Mock(side_effect=[{"rd_test"}, set()])
        monkeypatch.setattr(tmux, "session_targets", probe)
        monkeypatch.setattr(tmux, "session_exists", lambda target: False)
        monitor = TailLiveness()
        assert await monitor.exists("rd_test")
        monkeypatch.setenv("DUCKTERM_TMUX_SOCKET", "test-second")
        assert not await monitor.exists("rd_test")
        assert probe.call_count == 2

    asyncio.run(scenario())


def test_shared_probe_preserves_final_output_drain(tmp_path, monkeypatch):
    async def scenario():
        path = tmp_path / "pane.log"
        path.write_bytes(b"")
        bus = EventBus()
        sup = SessionSupervisor(
            bus=bus,
            runtime=GenericRuntime("cat"),
            session_key="test-final",
            cwd=str(tmp_path),
            extra={"test": True},
            tail_liveness=TailLiveness(),
        )
        sup._tmux_target = "rd_test-final"
        sup._pipe_path = str(path)
        monkeypatch.setattr(tmux, "session_targets", lambda: set())

        def dead(target):
            path.write_bytes(b"final output\r\n")
            return False

        monkeypatch.setattr(tmux, "session_exists", dead)
        await sup._tail_pipe()
        assert b"".join(sup._byte_tail) == b"final output\r\n"
        assert bus.recent()[-1]["event_type"] == "SessionEnd"

    asyncio.run(scenario())


def test_session_snapshot_preserves_names_and_has_session_semantics(monkeypatch):
    command = Mock(return_value=(True, "rd_alive\nrd_dead\nrd_alive_shell\nother\n"))
    monkeypatch.setattr(tmux, "_tmux", command)
    assert tmux.session_targets() == {"rd_alive", "rd_dead", "rd_alive_shell", "other"}
    command.assert_called_once_with("list-sessions", "-F", "#{session_name}")


@pytest.mark.parametrize(
    "error",
    [
        "no server running on /tmp/test/socket",
        "error connecting to /tmp/test/socket (No such file or directory)",
    ],
)
def test_absent_server_is_an_empty_snapshot(monkeypatch, error):
    monkeypatch.setattr(tmux, "_tmux", lambda *args: (False, error))
    assert tmux.session_targets() == set()


def test_socket_failure_is_not_an_empty_snapshot(monkeypatch):
    monkeypatch.setattr(tmux, "_tmux", lambda *args: (False, "Permission denied"))
    with pytest.raises(RuntimeError, match="cannot discover"):
        tmux.session_targets()
