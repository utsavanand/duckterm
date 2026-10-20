"""Complete an explicit checkpoint range using independent, resumable span writes."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from duckterm import memory_continuity, memory_spans
from duckterm.core import memory_folding
from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import fingerprint, transaction

if TYPE_CHECKING:
    from duckterm.core.saved_progress import ProgressCoordinator, SummaryUpdate


async def refresh(
    coordinator: ProgressCoordinator,
    key: str,
    captured: dict[str, Any],
    *,
    continue_if: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> SummaryUpdate:
    """Join a session's update without letting an HTTP disconnect cancel its work."""
    from duckterm.core.saved_progress import SummaryUpdate

    identity = fingerprint(
        [
            captured["source"],
            captured["conversation"],
            captured["policy"],
            memory_spans.boundary(captured),
            captured.get("checkpoint_settings"),
            sorted(
                (source, record, digest)
                for (source, record), digest in captured.get("checkpoint_selection", {}).items()
            ),
            captured.get("checkpoint_order"),
        ]
    )
    active = coordinator.span_refreshes.get(key)
    if active is not None and coordinator.span_refresh_inputs.get(key) != identity:
        return SummaryUpdate.failed("source_changed", diagnostic="different_active_capture")
    token = object()
    waiters = coordinator.span_waiters.setdefault(key, {})
    waiters[token] = continue_if
    if active is None:
        coordinator.span_progress.pop(key, None)
    if on_progress is not None:
        coordinator.span_observers.setdefault(key, {})[token] = on_progress
        if key in coordinator.span_progress:
            on_progress(dict(coordinator.span_progress[key]))
    if active is None:
        # Diagnostics belong to the initiating caller, even when later requests
        # join this job. They never participate in summary deduplication.
        trigger = dict(captured.get("trigger", {"kind": "internal_refresh"}))
        trigger.setdefault("range_id", identity)
        active = asyncio.create_task(_run(coordinator, key, {**captured, "trigger": trigger}))
        coordinator.span_refreshes[key] = active
        coordinator.span_refresh_inputs[key] = identity

        def finished(done: asyncio.Task[Any]) -> None:
            if coordinator.span_refreshes.get(key) is done:
                coordinator.span_refreshes.pop(key, None)
                coordinator.span_refresh_inputs.pop(key, None)
            if not done.cancelled():
                done.exception()

        active.add_done_callback(finished)
    try:
        result = await asyncio.shield(active)
        # Keep the original failure stage instead of masking it with a later fence.
        if result.state != "failed" and not current(coordinator, key, captured):
            return SummaryUpdate.failed(
                "source_changed",
                diagnostic="refresh_result:"
                + (change_reason(coordinator, key, captured) or "source_changed"),
            )
        return result
    finally:
        observers = coordinator.span_observers.get(key, {})
        observers.pop(token, None)
        if not observers:
            coordinator.span_observers.pop(key, None)
        waiters.pop(token, None)
        if not waiters:
            coordinator.span_waiters.pop(key, None)


def wanted(coordinator: ProgressCoordinator, key: str) -> bool:
    # A canceled preparation stops before the next batch; another dialog or an
    # accepted checkpoint may still need the same range. No new queue or timer.
    return any(check is None or check() for check in coordinator.span_waiters.get(key, {}).values())


def current(
    coordinator: ProgressCoordinator, key: str, captured: dict[str, Any], *, catalog: bool = False
) -> bool:
    return change_reason(coordinator, key, captured, catalog=catalog) is None


def change_reason(
    coordinator: ProgressCoordinator, key: str, captured: dict[str, Any], *, catalog: bool = False
) -> str | None:
    """Fixed diagnostics only; never persist paths, source contents or credentials."""
    from duckterm.core.saved_progress import file_version, policy_key

    if "checkpoint_selection" in captured:
        from duckterm import memory_checkpoint

        return memory_checkpoint.changed(coordinator.server, key, captured)
    if coordinator.facts(key) != captured["facts"]:
        return "facts_changed"
    if policy_key() != captured["policy"]:
        return "policy_changed"
    fence = captured.get("fence")
    if fence and file_version(fence["path"]) != fence["version"]:
        return "native_file_changed"
    for source in captured.get("memory_sources", []):
        if source.get("fence") and file_version(source["path"]) != source["fence"]:
            return "memory_file_changed"
    if catalog and captured.get("memory_catalog") != memory_continuity.original_catalog(
        coordinator.server.memory.catalog(key)
    ):
        return "original_catalog_changed"
    return None


async def _run(
    coordinator: ProgressCoordinator, key: str, captured: dict[str, Any]
) -> SummaryUpdate:
    from duckterm.core.saved_progress import SummaryUpdate

    if captured.get("memory_gaps"):
        return SummaryUpdate.failed("source_unavailable")
    saved = memory_folding.rows(coordinator.server.digests, key, captured["source"]["scope_hash"])
    leaves = [
        row
        for row in saved
        if row.get("span") == "incremental" and row.get("fold", {}).get("tier") == 0
    ]
    # A legacy cumulative paragraph cannot establish coverage of an original
    # prefix. Without an actual leaf boundary, begin with the original records.
    selection = captured.get("checkpoint_selection")
    covered = {}
    # Window checkpoints can leave older holes deliberately. Never mistake their
    # upper boundary for a claim that the entire preceding history was processed.
    for leaf in leaves:
        if selection is not None or "checkpoint_settings" in leaf:
            for source, records in leaf.get("continuity", {}).get("processed", {}).items():
                covered.update({(source, record): digest for record, digest in records.items()})
    if covered:
        captured = {**captured, "span_covered": covered}
    sequential = [
        leaf
        for leaf in leaves
        if "checkpoint_settings" not in leaf
        and not leaf.get("span_input", {}).get("selection_window")
    ]
    latest = leaves[-1] if leaves else None
    predecessor = sequential[-1] if sequential and selection is None else None
    if latest and latest.get("policy") != captured["policy"]:
        return SummaryUpdate.failed("source_changed", diagnostic="summary_policy_changed")
    lower = (
        predecessor["span_input"]["next_boundary"]
        if predecessor is not None
        else memory_spans.boundary(captured, empty=True)
    )
    changed = False
    total: int | None = None
    completed = 0
    batch_index = 0
    while True:
        if not wanted(coordinator, key):
            return SummaryUpdate.failed("update_failed", diagnostic="update_canceled")
        try:
            planned = memory_spans.plan(captured, lower)
        except ValueError as exc:
            return SummaryUpdate.failed("source_changed", diagnostic=str(exc))
        if total is None:
            total = len(planned["selected"]) + planned["pending_records"]
        coordinator.report_span_progress(key, stage="writing", completed=completed, total=total)
        coordinator.span_status[key] = {
            "completed_spans": len(leaves),
            "pending_records": len(planned["selected"]) + planned["pending_records"],
        }
        if not planned["selected"]:
            if planned["oversized"]:
                return SummaryUpdate.failed("invalid_context", diagnostic="oversized_record")
            break
        batch_index += 1
        batch = {
            **captured,
            "trigger": {
                **captured["trigger"],
                "batch_index": batch_index,
                "continuation": batch_index > 1,
            },
        }
        update, planned = await coordinator.write_span(key, batch, lower)
        if update.state not in {"updated", "reused"} or update.revision is None:
            return update
        completed += len(planned["selected"])
        coordinator.report_span_progress(key, completed=completed, stage="checking")
        latest = update.revision
        leaves.append(update.revision)
        changed = changed or update.state == "updated"
        lower = planned["next_boundary"]
        if not planned["pending_records"]:
            break
    if not wanted(coordinator, key):
        return SummaryUpdate.failed("update_failed", diagnostic="update_canceled")
    if latest is None:
        return SummaryUpdate.failed("no_transcript")
    coordinator.report_span_progress(key, stage="checking")
    try:
        final = await coordinator._validation_capture(key, captured)
        if final is None:
            return SummaryUpdate.failed("source_changed", diagnostic="publication:capture_unstable")
        final = await coordinator._validation_memory(key, final)
    except APIError as exc:
        return SummaryUpdate.failed(
            "source_changed" if exc.status == 409 else "source_unavailable",
            diagnostic=(
                "publication:memory_capture_conflict"
                if exc.status == 409
                else "publication:memory_capture_unavailable"
            ),
        )
    for name in ("source", "conversation", "policy"):
        if final[name] != captured[name]:
            return SummaryUpdate.failed(
                "source_changed", diagnostic="publication:" + name + "_changed"
            )
    # Publish only after the entire captured range completes. Every accepted leaf
    # already exists and survives a later batch's failure for an exact retry.
    conn = coordinator.server.history._conn
    with transaction(conn):
        if not current(coordinator, key, final, catalog=True):
            return SummaryUpdate.failed(
                "source_changed",
                diagnostic="publication:"
                + (change_reason(coordinator, key, final, catalog=True) or "source_changed"),
            )
        conn.execute(
            "UPDATE sessions SET progress=?,progress_at=?,outcome_summary=? WHERE session_key=?",
            (
                json.dumps({"summary": latest["summary"], "revision_id": latest["id"]}),
                int(time.time() * 1000),
                latest["summary"],
                key,
            ),
        )
    coordinator.span_status[key] = {"completed_spans": len(leaves), "pending_records": 0}
    originals = memory_spans.original_rows(captured)
    eligible = (
        len(selection)
        if selection is not None
        else sum(not row["reference_only"] for row in originals)
    )
    available = len(selection) if selection is not None else len(originals)
    return replace(
        SummaryUpdate.from_revision(latest, reused=not changed),
        span_coverage={
            "state": "complete",
            "revision_ids": [row["id"] for row in leaves],
            "available_records": available,
            "eligible_records": eligible,
            "summarized_records": eligible,
            "reference_only_records": available - eligible,
            **(
                {
                    "scope": (
                        "unprocessed_selection"
                        if captured.get("checkpoint_order")
                        else "recent_window"
                    ),
                    **(
                        {"order": captured["checkpoint_order"]}
                        if captured.get("checkpoint_order")
                        else {}
                    ),
                    "lookback_records": captured["checkpoint_settings"]["lookback_records"],
                    "history_eligible_records": captured["checkpoint_available"],
                    "outside_window_records": captured["checkpoint_available"] - eligible,
                    "captured_at": captured["captured_at"],
                }
                if selection is not None
                else {}
            ),
            "pending_records": 0,
        },
    )
