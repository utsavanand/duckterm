"""Age evidence from saved checkpoint outcomes, never from attempt timestamps."""

from __future__ import annotations

import sqlite3
from typing import Any


def project(checkpoints: list[dict[str, Any]]) -> dict[str, Any]:
    """Read the existing checkpoint list once; no clock table or provider call.

    Success is historical: later unavailable evidence does not rewrite the time
    an update succeeded. It also does not assert that a switch is ready now.
    Older records without a recorded outcome remain unknown rather than never.
    """
    unknown = False
    for cp in sorted(checkpoints, key=lambda row: row["created_at"], reverse=True):
        record = cp.get("record", {})
        update = record.get("summary_update")
        if not isinstance(update, dict):
            unknown = True
            continue
        if update.get("state") not in {"updated", "reused"}:
            continue
        if not update.get("revision_id") or update["revision_id"] != record.get("summary_ref"):
            unknown = True
            continue
        coverage = update.get("span_coverage")
        if coverage is not None and (
            coverage.get("state") != "complete" or coverage.get("pending_records") != 0
        ):
            continue
        return {"state": "known", "checkpoint_id": cp["id"], "succeeded_at": cp["created_at"]}
    return {"state": "unknown" if unknown else "never", "checkpoint_id": None, "succeeded_at": None}


def activity(
    conn: sqlite3.Connection,
    key: str,
    checkpoints: list[dict[str, Any]],
    *,
    clock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Count recorded events after the successful marker's capture, not save time.

    Run on the history connection's owner thread. Use the same event filter as
    the captured boundary; neither lifetime counts nor timestamps define it.
    This is activity since capture, not turns or unsummarized conversation.
    """
    from duckterm.persistence.saved_state import WORK_EVENTS_FILTER, is_status_observation

    clock = project(checkpoints) if clock is None else clock
    result: dict[str, Any] = {
        "state": clock["state"],
        "checkpoint_id": clock["checkpoint_id"],
        "events_since_capture": None,
        "event_filter": None,
        "reason": None,
    }
    if clock["state"] != "known":
        result["reason"] = (
            "no_successful_checkpoint"
            if clock["state"] == "never"
            else "checkpoint_outcome_unknown"
        )
        return result
    result.update(state="unknown", reason="boundary_unavailable")
    checkpoint = next((cp for cp in checkpoints if cp["id"] == clock["checkpoint_id"]), {})
    boundary = checkpoint.get("record", {}).get("events")
    if not isinstance(boundary, dict):
        return result
    last, identity, count = boundary.get("last"), boundary.get("last_id"), boundary.get("count")
    if (
        type(last) is not int
        or not 0 <= last <= 9223372036854775807
        or type(count) is not int
        or count < 0
        or (count == 0 and (last != 0 or identity is not None))
        or (count > 0 and (last == 0 or not isinstance(identity, str) or not identity))
    ):
        return result
    event_filter = boundary.get("filter")
    if event_filter not in (None, WORK_EVENTS_FILTER):
        result["reason"] = "unsupported_event_filter"
        return result
    if last:
        anchor = conn.execute(
            "SELECT id FROM events WHERE rowid=? AND session_key=?", (last, key)
        ).fetchone()
        if anchor is None or anchor[0] != identity:
            result["reason"] = "boundary_missing"
            return result
    # Ordinary work rows need only a count. Decode only the observation types
    # whose payload determines whether they were excluded by work-v1.
    if event_filter == WORK_EVENTS_FILTER:
        total = conn.execute(
            "SELECT count(*) FROM events WHERE session_key=? AND rowid>? "
            "AND event_type NOT IN ('Attended','Notification')",
            (key, last),
        ).fetchone()[0]
        observations = conn.execute(
            "SELECT event_type,payload_json FROM events WHERE session_key=? AND rowid>? "
            "AND event_type IN ('Attended','Notification')",
            (key, last),
        )
        total += sum(not is_status_observation(row[0], row[1]) for row in observations)
    else:
        total = conn.execute(
            "SELECT count(*) FROM events WHERE session_key=? AND rowid>?", (key, last)
        ).fetchone()[0]
    result.update(
        state="known",
        events_since_capture=total,
        event_filter=event_filter or "all-events",
        reason=None,
    )
    return result
