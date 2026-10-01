"""Deleting a parent detaches its children without deleting their history or process."""

import asyncio
import json

from duckterm.core.eventbus import EventBus
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server


def seed(bus, key, parent=None):
    return bus.publish(
        {
            "event_type": "SessionStart",
            "session_key": key,
            "parent_session_key": parent,
            "test": True,
        }
    )


def test_delete_detaches_only_direct_children_and_late_events_cannot_reattach(tmp_path):
    store = HistoryStore(tmp_path / "db.sqlite")
    bus = EventBus(sink=store.record)
    try:
        seed(bus, "parent")
        original = seed(bus, "child", "parent")
        seed(bus, "grandchild", "child")
        assert store.delete_session("parent")
        assert store.session("child")["parent_session_key"] is None
        assert store.session("grandchild")["parent_session_key"] == "child"
        assert store.events_for("child")[0]["parent_session_key"] == "parent"
        late = seed(bus, "child", "parent")
        assert late["parent_session_key"] is None
        assert store.session("child")["parent_session_key"] is None
        assert original["parent_session_key"] == "parent"
        assert {row["session_key"]: row["parent_session_key"] for row in store.fork_tree()} == {
            "child": None,
            "grandchild": "child",
        }
        # A child first seen after parent deletion must not acquire a dead link.
        seed(bus, "late-child", "parent")
        assert store.session("late-child")["parent_session_key"] is None
    finally:
        store.purge_test_sessions()
        store.close()


def test_reopen_repairs_old_deleted_links_but_preserves_unknown_parent(tmp_path):
    db = tmp_path / "db.sqlite"
    store = HistoryStore(db)
    bus = EventBus(sink=store.record)
    seed(bus, "parent")
    seed(bus, "child", "parent")
    seed(bus, "pending-child", "parent-not-seen-yet")
    store.delete_session("parent")
    store._conn.execute("UPDATE sessions SET parent_session_key='parent' WHERE session_key='child'")
    store._conn.commit()
    store.close()
    store = HistoryStore(db)
    try:
        assert store.session("child")["parent_session_key"] is None
        assert store.session("pending-child")["parent_session_key"] == "parent-not-seen-yet"
    finally:
        store.purge_test_sessions()
        store.close()


class Writer:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass


def test_parent_delete_keeps_live_child_responsive(tmp_path, monkeypatch):
    # Exercise real subprocesses without touching the user's tmux sessions.
    monkeypatch.setattr("duckterm.core.orchestrator.tmux.has_tmux", lambda: False)

    async def scenario():
        store = HistoryStore(tmp_path / "db.sqlite")
        server = Server(history=store)
        try:
            for key, parent in [("parent", None), ("child", "parent")]:
                await server.orchestrator.launch(
                    runtime=GenericRuntime("cat"),
                    cwd=str(tmp_path),
                    session_key=key,
                    parent_session_key=parent,
                    test=True,
                )
            child = server.orchestrator.get("child")
            assert child is not None and child._proc is not None
            child_pid = child._proc.pid
            writer = Writer()
            await server._delete_session(writer, "parent", b"{}")
            assert json.loads(writer.data.split(b"\r\n\r\n", 1)[1])["deleted"] is True
            assert store.session("parent") is None
            assert store.session("child")["parent_session_key"] is None
            assert child._proc.pid == child_pid and child._proc.returncode is None
            assert child.write_bytes(b"child still responds\n")
            async with asyncio.timeout(3):
                while b"child still responds" not in b"".join(child._byte_tail):
                    await asyncio.sleep(0.01)
            # Real supervisor events carry the old parent in _extra; persistence
            # and live fan-out must both reject that stale metadata.
            child._emit("Stop")
            assert store.session("child")["parent_session_key"] is None
            assert server.bus.recent(1)[0]["parent_session_key"] is None
        finally:
            await server.orchestrator.stop("parent")
            await server.orchestrator.stop("child")
            store.purge_test_sessions()
            store.close()

    asyncio.run(scenario())
