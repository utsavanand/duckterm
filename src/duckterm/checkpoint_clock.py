"""Age evidence from saved checkpoint outcomes, never from attempt timestamps."""

from __future__ import annotations

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
