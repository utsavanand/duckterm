"""A failed attempt must not make days-old memory appear freshly checkpointed."""

import asyncio
import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace

import pytest

from duckterm.checkpoint_clock import activity, project
from duckterm.persistence.saved_state import event_source


def checkpoint(identity, at, state):
    return {
        "id": identity,
        "created_at": at,
        "record": {
            "summary_ref": "revision",
            "summary_update": {
                "state": state,
                "revision_id": "revision",
            },
        },
    }


def test_clock_uses_success_not_newer_failure_partial_or_unknown():
    rows = [
        checkpoint("good", 100, "updated"),
        checkpoint("failed", 200, "failed"),
        checkpoint("partial", 300, "partial"),
        {"id": "legacy", "created_at": 400},
    ]
    before = deepcopy(rows)
    assert project(rows) == {"state": "known", "checkpoint_id": "good", "succeeded_at": 100}
    assert rows == before


def test_never_is_distinct_from_legacy_unknown():
    assert project([])["state"] == "never"
    assert project([checkpoint("failed", 200, "failed")])["state"] == "never"
    assert project([{"id": "legacy", "created_at": 1}])["state"] == "unknown"


def test_success_requires_matching_revision_and_completed_recorded_range():
    good = checkpoint("good", 100, "updated")
    broken = checkpoint("broken", 200, "updated")
    broken["record"]["summary_ref"] = "different"
    partial = checkpoint("partial", 300, "updated")
    partial["record"]["summary_update"]["span_coverage"] = {
        "state": "complete",
        "pending_records": 2,
    }
    assert project([good, broken, partial])["checkpoint_id"] == "good"
    reused = checkpoint("reused", 400, "reused")
    reused["record"]["summary_update"]["span_coverage"] = {
        "state": "complete",
        "pending_records": 0,
    }
    assert project([good, reused])["succeeded_at"] == 400


@pytest.fixture
def events():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE events (id TEXT PRIMARY KEY, session_key TEXT, "
        "event_type TEXT, payload_json TEXT, ts INTEGER)"
    )
    yield conn
    conn.close()


def add_event(conn, identity, *, key="test-agent", kind="PostToolUse", at=1, extra=None):
    payload = {"event_type": kind, "test": True, **(extra or {})}
    conn.execute(
        "INSERT INTO events VALUES (?,?,?,?,?)", (identity, key, kind, json.dumps(payload), at)
    )


def captured_checkpoint(conn, *, work_only=True):
    cp = checkpoint("saved", 1000, "updated")
    cp["record"]["events"] = event_source(conn, "test-agent", work_only=work_only)
    return cp


def test_activity_counts_after_capture_even_before_save_or_with_older_timestamp(events):
    add_event(events, "captured", at=100)
    cp = captured_checkpoint(events)
    add_event(events, "during-summary", at=500)
    add_event(events, "late-old-timestamp", at=50)
    add_event(events, "other-session", key="other-test-agent", at=1500)
    latest_failed = checkpoint("failed", 2000, "failed")
    assert activity(events, "test-agent", [latest_failed, cp]) == {
        "state": "known",
        "checkpoint_id": "saved",
        "events_since_capture": 2,
        "event_filter": "work-v1",
        "reason": None,
    }


def test_work_filter_excludes_only_content_free_observations(events):
    add_event(events, "anchor")
    cp = captured_checkpoint(events)
    add_event(events, "attended", kind="Attended")
    add_event(events, "idle", kind="Notification", extra={"notification_type": "idle_prompt"})
    add_event(events, "approval", kind="Notification", extra={"notification_type": "permission"})
    add_event(events, "new-payload", kind="Attended", extra={"unknown_content": "Keep this"})
    add_event(events, "tool")
    add_event(events, "malformed", kind="Notification")
    events.execute("UPDATE events SET payload_json='not json' WHERE id='malformed'")
    assert activity(events, "test-agent", [cp])["events_since_capture"] == 4


def test_legacy_unfiltered_marker_keeps_its_all_event_meaning(events):
    add_event(events, "anchor")
    cp = captured_checkpoint(events, work_only=False)
    add_event(events, "idle", kind="Notification")
    result = activity(events, "test-agent", [cp])
    assert result["event_filter"] == "all-events"
    assert result["events_since_capture"] == 1


@pytest.mark.parametrize("replacement", ["deleted", "different-id", "different-session"])
def test_lost_or_reused_boundary_is_unknown_not_a_zero_count(events, replacement):
    add_event(events, "anchor")
    cp = captured_checkpoint(events)
    if replacement == "deleted":
        events.execute("DELETE FROM events")
    elif replacement == "different-id":
        events.execute("UPDATE events SET id='replacement'")
    else:
        events.execute("UPDATE events SET session_key='other-test-agent'")
    result = activity(events, "test-agent", [cp])
    assert result["state"] == "unknown" and result["events_since_capture"] is None
    assert result["reason"] == "boundary_missing"


def test_earlier_deletion_does_not_distort_events_after_the_retained_anchor(events):
    add_event(events, "earlier")
    add_event(events, "anchor")
    cp = captured_checkpoint(events)
    events.execute("DELETE FROM events WHERE id='earlier'")
    add_event(events, "new-work")
    assert activity(events, "test-agent", [cp])["events_since_capture"] == 1


def test_empty_capture_is_a_valid_zero_boundary(events):
    cp = captured_checkpoint(events)
    assert activity(events, "test-agent", [cp])["events_since_capture"] == 0
    add_event(events, "first-work")
    assert activity(events, "test-agent", [cp])["events_since_capture"] == 1


def test_never_unknown_legacy_and_missing_anchor_remain_distinct(events):
    assert activity(events, "test-agent", [])["reason"] == "no_successful_checkpoint"
    assert activity(events, "test-agent", [{"id": "legacy", "created_at": 1}])["reason"] == (
        "checkpoint_outcome_unknown"
    )
    assert activity(events, "test-agent", [checkpoint("legacy", 1, "updated")])["reason"] == (
        "boundary_unavailable"
    )


@pytest.mark.parametrize(
    "patch,reason",
    [
        ({"last": True}, "boundary_unavailable"),
        ({"last": 2**63}, "boundary_unavailable"),
        ({"count": 0}, "boundary_unavailable"),
        ({"last_id": None}, "boundary_unavailable"),
        ({"filter": "future-filter"}, "unsupported_event_filter"),
    ],
)
def test_malformed_or_unknown_boundaries_are_not_guessed(events, patch, reason):
    add_event(events, "anchor")
    cp = captured_checkpoint(events)
    cp["record"]["events"].update(patch)
    result = activity(events, "test-agent", [cp])
    assert result["state"] == "unknown" and result["events_since_capture"] is None
    assert result["reason"] == reason


def test_checkpoint_endpoint_exposes_clock_and_activity_without_writing(events):
    from tests.runtime.test_session_api import Writer

    from duckterm.server import Server

    add_event(events, "anchor")
    cp = captured_checkpoint(events)
    add_event(events, "later")
    original = deepcopy(cp)
    changed = events.total_changes
    server = SimpleNamespace(history=SimpleNamespace(_conn=events, checkpoints=lambda key: [cp]))
    writer = Writer()
    asyncio.run(Server._list_checkpoints(server, writer, "test-agent"))
    response = json.loads(writer.data.split(b"\r\n\r\n", 1)[1])
    assert response["checkpoint_clock"]["checkpoint_id"] == "saved"
    assert response["checkpoint_activity"]["events_since_capture"] == 1
    assert cp == original and events.total_changes == changed
