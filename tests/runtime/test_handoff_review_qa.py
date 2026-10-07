"""Independent exact-boundary approval races and durable failure checks."""

import asyncio
import json
import sqlite3
import time

import pytest
from tests.runtime.test_handoff_review import approval, approved
from tests.runtime.test_saved_checkpoint import rig

from duckterm import harness_switch
from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore

__all__ = ["rig"]


def test_distinct_previews_cannot_replace_each_other_without_new_review(rig):
    server, _, calls = rig

    async def run():
        first = await server.handoff_review.prepare(
            "test-save", {"summary": "First owner brief", "gaps": []}
        )
        second = await server.handoff_review.prepare(
            "test-save", {"summary": "Different owner brief", "gaps": []}
        )
        saved = await server.handoff_review.approve("test-save", approval(first))
        with pytest.raises(APIError, match="Progress changed"):
            await server.handoff_review.approve("test-save", approval(second))
        assert (
            json.loads(server.history.session("test-save")["progress"])["revision_id"]
            == saved["revision_id"]
        )
        assert len(server.history.checkpoints("test-save")) == 1
        assert calls == []

    asyncio.run(run())


def test_final_cache_write_failure_rolls_back_revision_and_marker_on_reopen(rig, tmp_path):
    server, _, _ = rig
    conn = server.history._conn
    conn.execute(
        "CREATE TRIGGER fail_review_cache BEFORE UPDATE OF progress ON sessions "
        "BEGIN SELECT RAISE(ABORT, 'cache failed'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="cache failed"):
        asyncio.run(approved(server))
    reopened = HistoryStore(tmp_path / "db.sqlite")
    try:
        assert reopened._conn.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 0
        assert reopened.checkpoints("test-save") == []
        assert reopened.session("test-save")["progress"] is None
    finally:
        reopened.close()


def test_deleted_approved_event_cannot_be_hidden_by_cached_review(rig):
    server, _, _ = rig

    async def run():
        _, saved = await approved(server)
        server.history._conn.execute("DELETE FROM events WHERE session_key='test-save'")
        server.history._conn.commit()
        historical = server.history.checkpoints("test-save")[0]
        assert not historical["handoff_eligible"]
        assert "source_missing" in historical["reason_codes"]
        with pytest.raises(APIError, match="not ready"):
            await harness_switch.prepare(server, "test-save", server.history.session("test-save"))
        assert server.digests.revision("test-save", saved["revision_id"]) is not None

    asyncio.run(run())


def test_approved_boundary_protects_expired_events_after_two_reopens(rig, tmp_path):
    server, _, _ = rig
    conn = server.history._conn
    old = int((time.time() - 31 * 86400) * 1000)
    conn.execute("UPDATE events SET ts=? WHERE session_key='test-save'", (old,))
    conn.commit()
    _, saved = asyncio.run(approved(server))
    for _ in range(2):
        reopened = HistoryStore(tmp_path / "db.sqlite")
        try:
            assert (
                reopened._conn.execute(
                    "SELECT count(*) FROM events WHERE session_key='test-save'"
                ).fetchone()[0]
                == 1
            )
            cp = reopened.checkpoints("test-save")[0]
            assert cp["handoff_eligible"]
            assert cp["record"]["summary_ref"] == saved["revision_id"]
        finally:
            reopened.close()
