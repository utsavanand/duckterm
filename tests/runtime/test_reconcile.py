"""Startup reconciliation: after a reboot (which kills tmux), a launched session
the DB thinks is running has no live pane, and event-driven state can never flip
it — so it would show 'busy'/'idle' forever. reconcile() marks those interrupted
(resumable, honest) while leaving genuinely-live sessions alone."""

import asyncio

import pytest

import duckterm.core.orchestrator as orch_mod
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.helpers.session_credentials import credential_path
from duckterm.persistence.history import HistoryStore


def _seed_launched_running(store: HistoryStore, key: str) -> None:
    """A launched session the DB believes is running (busy)."""
    store.record(
        {
            "event_type": "SessionStart",
            "session_key": key,
            "launched": True,
            "test": True,
            "_ts": 1,
            "_id": key,
        }
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


def test_reboot_zombie_is_marked_interrupted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tmux is gone (reboot) → no live sessions → a launched 'busy' row is
    reconciled to 'interrupted' (died without a clean stop), not left claiming it's running."""
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: True)
    monkeypatch.setattr(orch_mod.tmux, "list_duckterm_sessions", lambda: [])
    store = HistoryStore(tmp_path / "db.sqlite")
    _seed_launched_running(store, "zombie")
    assert store.session("zombie")["state"] == "busy"

    orch = Orchestrator(EventBus(), history=store)
    adopted = asyncio.run(orch.reconcile())

    assert adopted == []
    assert store.session("zombie")["state"] == "interrupted"  # honest, and resumable


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
    assert store.session("dead")["state"] == "interrupted"  # no live pane → reconciled


def test_at_rest_and_watched_sessions_are_untouched(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconciliation only touches LAUNCHED, non-at-rest rows: an already-stopped
    session, and a watched (launched=0) session, are both left as-is."""
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: True)
    monkeypatch.setattr(orch_mod.tmux, "list_duckterm_sessions", lambda: [])
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


@pytest.mark.parametrize(
    "event_type,expected",
    [("Stop", "idle"), ("PreToolUse", "busy"), ("PermissionRequest", "waiting")],
)
def test_live_interrupted_session_recovers_without_relaunch(
    tmp_path, monkeypatch, event_type, expected
):
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: True)
    monkeypatch.setattr(orch_mod.tmux, "list_duckterm_sessions", lambda: ["alive"])

    async def fake_reattach(self):
        return None

    monkeypatch.setattr(orch_mod.SessionSupervisor, "reattach", fake_reattach)
    store = HistoryStore(tmp_path / "db.sqlite")
    _seed_launched_running(store, "alive")
    store.set_state("alive", "interrupted", now=3)
    # Hooks can still arrive while the stale interruption blocks state changes.
    store.record({"event_type": event_type, "session_key": "alive", "_ts": 4, "_id": "latest"})
    before = store.session("alive")
    assert before["state"] == "interrupted"
    credential = credential_path("alive", store.session_api.credential_dir)
    assert not credential.exists()
    orch = Orchestrator(EventBus(sink=store.record), history=store)
    assert asyncio.run(orch.reconcile()) == ["alive"]
    after = store.session("alive")
    assert after["state"] == expected
    assert credential.exists()
    assert after["ended_at"] is None
    for field in ("started_at", "updated_at", "event_count", "last_event_type"):
        assert after[field] == before[field]
    store.record({"event_type": "PostToolUse", "session_key": "alive", "_ts": 5, "_id": "next"})
    assert store.session("alive")["state"] == "busy"


@pytest.mark.parametrize("state", ["stopped", "archived"])
def test_live_pane_does_not_undo_intentional_user_state(tmp_path, monkeypatch, state):
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: True)
    monkeypatch.setattr(orch_mod.tmux, "list_duckterm_sessions", lambda: ["alive"])

    async def fake_reattach(self):
        return None

    monkeypatch.setattr(orch_mod.SessionSupervisor, "reattach", fake_reattach)
    store = HistoryStore(tmp_path / "db.sqlite")
    _seed_launched_running(store, "alive")
    store.set_state("alive", state, now=3)
    asyncio.run(Orchestrator(EventBus(), history=store).reconcile())
    assert store.session("alive")["state"] == state


@pytest.mark.parametrize("available", [True, False])
def test_unavailable_discovery_does_not_interrupt_sessions(tmp_path, monkeypatch, available):
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: available)

    def fail():
        raise RuntimeError("socket permission denied")

    monkeypatch.setattr(orch_mod.tmux, "list_duckterm_sessions", fail)
    store = HistoryStore(tmp_path / "db.sqlite")
    _seed_launched_running(store, "alive")
    assert asyncio.run(Orchestrator(EventBus(), history=store).reconcile()) == []
    assert store.session("alive")["state"] == "busy"
