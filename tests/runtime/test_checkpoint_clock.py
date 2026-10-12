"""A failed attempt must not make days-old memory appear freshly checkpointed."""

from copy import deepcopy

from duckterm.checkpoint_clock import project


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
