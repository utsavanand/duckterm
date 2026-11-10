"""Private journal limits hold atomically across admission, replay and retention."""

import hashlib
import json
import sqlite3
import uuid

import pytest

from duckterm.mobile.analysis_store import RETENTION_MS, WORK_MS, AnalysisError, AnalysisStore


def row(now, *, device="phone", host="host", status="queued"):
    request = str(uuid.uuid4())
    return {
        "host": host,
        "device": device,
        "request_id": request,
        "context_id": "context",
        "scope": '{"kind":"oracle"}',
        "binding": "binding",
        "configuration": "config",
        "revision": 1,
        "payload_hash": hashlib.sha256(request.encode()).hexdigest(),
        "status": status,
        "reason": None,
        "question": "Question",
        "answer": None,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + WORK_MS,
        "captured_at": None,
        "source_count": 0,
        "truncated": 0,
    }


@pytest.fixture
def store(tmp_path):
    value = AnalysisStore(tmp_path)
    value.initialize(1000000)
    return value


def finish(store, value, now=None):
    assert store.transition(value, "complete", now or value["created_at"], answer="Answer")


def test_eight_queued_and_one_active_per_device(store):
    first = row(1000000)
    store.insert(first)
    with pytest.raises(AnalysisError, match="queue_limit"):
        store.insert(row(1000000))
    assert store.transition(first, "running", 1000000)
    for number in range(8):
        store.insert(row(1000000, device=f"other-{number}"))
    with pytest.raises(AnalysisError, match="queue_limit"):
        store.insert(row(1000000, device="ninth"))
    # Replay is free even when the queue is full.
    assert store.insert(first)["request_id"] == first["request_id"]
    with pytest.raises(AnalysisError, match="request_conflict"):
        store.insert({**first, "payload_hash": "changed"})


@pytest.mark.parametrize("limit", ["minute", "device_day", "host_day"])
def test_rate_limits_are_shared_across_scopes_and_contexts(store, limit):
    count = {"minute": 4, "device_day": 40, "host_day": 100}[limit]
    for index in range(count):
        timestamp = 1000000 + index * (0 if limit == "minute" else 61000)
        value = row(timestamp, device=f"phone-{index}" if limit == "host_day" else "phone")
        if index % 2:
            value["scope"] = '{"kind":"ungrouped"}'
            value["context_id"] = "another-context"
        store.insert(value)
        finish(store, value)
    candidate = row(timestamp + 1, device="new-phone" if limit == "host_day" else "phone")
    with pytest.raises(AnalysisError, match="rate_limit"):
        store.insert(candidate)
    # Exact replay still costs no rate record at the boundary.
    assert store.insert(value)["status"] == "complete"
    candidate["created_at"] += RETENTION_MS
    candidate["updated_at"] = candidate["created_at"]
    candidate["expires_at"] = candidate["created_at"] + WORK_MS
    store.insert(candidate)


def test_queue_work_deadlines_are_distinct_from_completed_retention(store):
    queued, running, completed = (row(1000000, device=str(i)) for i in range(3))
    for item in (queued, running, completed):
        store.insert(item)
    assert store.transition(running, "running", 1000001)
    finish(store, completed)
    now = 1000000 + 120001
    assert store.find("host", "0", queued["request_id"], now)["reason"] == "queue_expired"
    assert store.find("host", "1", running["request_id"], now)["status"] == "running"
    now = 1000000 + WORK_MS + 1
    assert store.find("host", "1", running["request_id"], now)["reason"] == "work_expired"
    assert store.find("host", "2", completed["request_id"], now)["answer"] == "Answer"
    assert store.find("host", "2", completed["request_id"], 1000000 + RETENTION_MS) is None


def test_completed_content_cap_keeps_idempotency_and_bounded_history(store):
    first = None
    for index in range(101):
        value = row(
            1000000 + index * 61000, host=f"host-{index // 100}", device=f"phone-{index // 40}"
        )
        value["question"] = "é" * 2048
        store.insert(value)
        assert store.transition(value, "complete", value["created_at"], answer="é" * 16384)
        first = first or value
    pruned = store.find("host", "unused", "unused", value["created_at"])
    assert pruned is None
    with store.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM questions WHERE answer IS NOT NULL").fetchone()[0]
            == 100
        )
        old = dict(
            db.execute(
                "SELECT * FROM questions WHERE request_id=?", (first["request_id"],)
            ).fetchone()
        )
        assert old["status"] == "expired" and old["reason"] == "history_pruned"
        assert old["answer"] is None and old["payload_hash"] == first["payload_hash"]
        assert db.execute("PRAGMA max_page_count").fetchone()[0] == 2048
        assert db.execute("PRAGMA secure_delete").fetchone()[0] == 1
    query = {**value, "host": "host-0", "device": "phone-1"}
    history = store.history(query, value["created_at"])
    assert len(history) == 20
    assert len(json.dumps(history, ensure_ascii=False).encode()) <= 1024 * 1024
    assert all(item["status"] == "complete" and item["answer"] for item in history)


def test_failed_transition_cannot_resurrect_invalidated_answer(store):
    value = row(1000000)
    store.insert(value)
    assert store.transition(value, "running", 1000001)
    value["status"] = "running"
    store.invalidate(1000002, reason="configuration_changed", completed=True)
    assert not store.transition(value, "complete", 1000003, answer="Late result")
    saved = store.find("host", "phone", value["request_id"], 1000003)
    assert saved["status"] == "cancelled" and saved["answer"] is None


def test_symlink_journal_is_refused_without_touching_target(tmp_path):
    target = tmp_path / "untouched"
    target.write_text("Owner content fixture")
    value = AnalysisStore(tmp_path)
    value.root.mkdir()
    value.path.symlink_to(target)
    with pytest.raises(OSError):
        value.initialize(1000000)
    assert target.read_text() == "Owner content fixture"


def test_storage_lock_is_bounded_and_has_no_partial_admission(store):
    with store.connect() as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.insert(row(1000000))
    assert store.queued(1000000) == []


def test_wall_clock_adjustment_preserves_native_receipt_ordering(store):
    value = row(1000000)
    store.insert(value)
    assert store.transition(value, "running", 999000, captured_at=1000000)
    value["status"] = "running"
    assert store.transition(value, "complete", 999001, captured_at=1000000, answer="Answer")
    saved = store.find("host", "phone", value["request_id"], 999002)
    assert saved["created_at"] <= saved["captured_at"] <= saved["updated_at"]
