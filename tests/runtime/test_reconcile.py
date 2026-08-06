"""Startup reconciliation: after a reboot (which kills tmux), a launched session
the DB thinks is running has no live pane, and event-driven state can never flip
it — so it would show 'busy'/'idle' forever. reconcile() marks those stopped
(resumable, honest) while leaving genuinely-live sessions alone."""

import asyncio

import pytest

import duckterm.core.orchestrator as orch_mod
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.persistence.history import HistoryStore


def _seed_launched_running(store: HistoryStore, key: str) -> None:
    """A launched session the DB believes is running (busy)."""
    store.record(
        {"event_type": "SessionStart", "session_key": key, "launched": True, "_ts": 1, "_id": key}
    )
    store.record(
        {
            "event_type": "PreToolUse",
            "session_key": key,
            "launched": True,
            "_ts": 2,
            "_id": key + "b",
        }
    )


def test_reboot_zombie_is_marked_stopped(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tmux is gone (reboot) → no live sessions → a launched 'busy' row is
    reconciled to 'stopped', not left claiming it's running."""
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: False)
    store = HistoryStore(tmp_path / "db.sqlite")
    _seed_launched_running(store, "zombie")
    assert store.session("zombie")["state"] == "busy"

    orch = Orchestrator(EventBus(), history=store)
    adopted = asyncio.run(orch.reconcile())

    assert adopted == []
    assert store.session("zombie")["state"] == "stopped"  # honest, and resumable


def test_live_tmux_session_is_adopted_not_swept(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A launched session whose tmux pane survived (laptop slept, no reboot) is
    re-adopted and left running — never marked stopped."""
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: True)
    monkeypatch.setattr(orch_mod.tmux, "list_duckterm_sessions", lambda: ["alive"])
    # reattach() would tail a real pipe; stub it so the test stays unit-level.

    async def fake_reattach(self) -> None:  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(orch_mod.SessionSupervisor, "reattach", fake_reattach)

    store = HistoryStore(tmp_path / "db.sqlite")
    _seed_launched_running(store, "alive")
    _seed_launched_running(store, "dead")  # a second one with no live pane

    orch = Orchestrator(EventBus(), history=store)
    adopted = asyncio.run(orch.reconcile())

    assert adopted == ["alive"]
    assert store.session("alive")["state"] == "busy"  # adopted, still running
    assert store.session("dead")["state"] == "stopped"  # no live pane → reconciled


def test_at_rest_and_watched_sessions_are_untouched(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconciliation only touches LAUNCHED, non-at-rest rows: an already-stopped
    session, and a watched (launched=0) session, are both left as-is."""
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: False)
    store = HistoryStore(tmp_path / "db.sqlite")

    _seed_launched_running(store, "stopme")
    store.set_state("stopme", "stopped")  # already at rest

    # A watched session (not launched) — its liveness is the PID sweep's job,
    # not reconcile's; must not be swept here.
    store.record({"event_type": "SessionStart", "session_key": "watched", "_ts": 1, "_id": "w"})
    store.record({"event_type": "PreToolUse", "session_key": "watched", "_ts": 2, "_id": "w2"})
    assert store.session("watched")["state"] == "busy"

    orch = Orchestrator(EventBus(), history=store)
    asyncio.run(orch.reconcile())

    assert store.session("stopme")["state"] == "stopped"  # unchanged
    assert store.session("watched")["state"] == "busy"  # watched: left for PID sweep
