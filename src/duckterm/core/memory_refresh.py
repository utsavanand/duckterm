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

# Four concurrent writer calls each held their ~20 s latency in measurement.
PARALLEL_SPANS = 4


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
            return replace(
                SummaryUpdate.failed(
                    "source_changed",
                    diagnostic="refresh_result:"
                    + (change_reason(coordinator, key, captured) or "source_changed"),
                ),
                span_coverage=result.attempt_coverage,
                attempt_coverage=result.attempt_coverage,
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


async def fold(coordinator: ProgressCoordinator, key: str, captured: dict[str, Any]) -> None:
    """Fold once every span is saved, group by group; a rejected fold stops the pass
    and keeps its spans unfolded until the next checkpoint."""
    async with coordinator.persist_locks.setdefault(key, asyncio.Lock()):
        for _ in range(memory_folding.MAX_FOLDS):
            proposal = await memory_folding.prepare(
                coordinator, key, captured, {"span": "incremental", "context_version": 2}
            )
            if not proposal or not proposal.get("parents"):
                return
            with transaction(coordinator.server.digests._conn):
                status = memory_folding.apply(
                    coordinator.server.digests, key, proposal, int(time.time() * 1000)
                )
            if status["state"] != "compacted":
                return


def reconcile_reply(prompt: str, model: str | None) -> str:
    from duckterm.server import summarize

    reply = summarize(prompt, tier="fast", **({"claude_model": model} if model else {}))
    if getattr(reply, "failure_reason", None):
        raise RuntimeError("reconcile_provider_failed")
    return str(reply.text)


async def reconcile(
    coordinator: ProgressCoordinator,
    key: str,
    captured: dict[str, Any],
    leaves: list[dict[str, Any]],
) -> dict[str, Any]:
    from duckterm import memory_reconcile

    model = captured.get("checkpoint_settings", {}).get("writer_model")

    async def generate(prompt: str) -> str:
        return await asyncio.to_thread(reconcile_reply, prompt, model)

    async with coordinator.persist_locks.setdefault(key, asyncio.Lock()):
        return await memory_reconcile.reconcile(
            coordinator.server.digests,
            key,
            captured["source"]["scope_hash"],
            leaves,
            generate,
        )


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
    # Spans written in parallel are saved out of order; continue after the span
    # furthest along the conversation, not the one saved last.
    sequential.sort(key=lambda leaf: memory_folding.span_position(leaf) or 0)
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
    completed_ids: list[str] = []

    def attempt_coverage() -> dict[str, Any] | None:
        # These are accepted writes from this attempt, not a replacement for
        # the last successful checkpoint or a certificate of current readiness.
        # Do not include unrelated leaves loaded before this attempt.
        if total is None:
            return None
        return {
            "state": "incomplete",
            "scope": "refresh_attempt",
            "revision_ids": list(completed_ids),
            "available_records": total,
            "eligible_records": total,
            "summarized_records": completed,
            "pending_records": total - completed,
            "reference_only_records": 0,
        }

    def incomplete(update: SummaryUpdate) -> SummaryUpdate:
        return replace(
            update, span_coverage=attempt_coverage(), attempt_coverage=attempt_coverage()
        )

    # Plan every span of the frozen capture first; planning reads only the
    # capture and the coverage loaded above, never the writes below.
    spans: list[tuple[dict[str, Any], dict[str, Any]]] = []
    cursor = lower
    while True:
        if not wanted(coordinator, key):
            return incomplete(SummaryUpdate.failed("update_failed", diagnostic="update_canceled"))
        try:
            planned = memory_spans.plan(captured, cursor)
        except ValueError as exc:
            return incomplete(SummaryUpdate.failed("source_changed", diagnostic=str(exc)))
        if total is None:
            total = len(planned["selected"]) + planned["pending_records"]
        if not planned["selected"]:
            if planned["oversized"]:
                return incomplete(
                    SummaryUpdate.failed("invalid_context", diagnostic="oversized_record")
                )
            break
        spans.append((cursor, planned))
        cursor = planned["next_boundary"]
        if not planned["pending_records"]:
            break
    coordinator.report_span_progress(key, stage="writing", completed=0, total=total)
    coordinator.span_status[key] = {"completed_spans": len(leaves), "pending_records": total}
    slots = asyncio.Semaphore(PARALLEL_SPANS)

    async def write(index: int, start: dict[str, Any]) -> tuple[SummaryUpdate, dict[str, Any]]:
        nonlocal completed
        async with slots:
            if not wanted(coordinator, key):
                return SummaryUpdate.failed("update_failed", diagnostic="update_canceled"), {}
            batch = {
                **captured,
                "trigger": {**captured["trigger"], "batch_index": index, "continuation": index > 1},
            }
            update, planned = await coordinator.write_span(key, batch, start)
        if update.state in {"updated", "reused"} and update.revision is not None:
            completed += len(planned["selected"])
            coordinator.report_span_progress(key, completed=completed, stage="writing")
        return update, planned

    coordinator.span_batches.add(key)
    try:
        results = await asyncio.gather(
            *(write(index, start) for index, (start, _) in enumerate(spans, 1))
        )
    finally:
        coordinator.span_batches.discard(key)
    failure = None
    for update, _ in results:
        if update.state not in {"updated", "reused"} or update.revision is None:
            failure = failure or update
            continue
        # Every accepted leaf is kept even when a sibling fails; a retry reuses it.
        completed_ids.append(update.revision["id"])
        latest = update.revision
        leaves.append(update.revision)
        changed = changed or update.state == "updated"
    if failure is not None:
        return incomplete(failure)
    if not wanted(coordinator, key):
        return incomplete(SummaryUpdate.failed("update_failed", diagnostic="update_canceled"))
    if latest is None:
        return incomplete(SummaryUpdate.failed("no_transcript"))
    await fold(coordinator, key, captured)
    coordinator.report_span_progress(key, stage="reconciling")
    try:
        await reconcile(coordinator, key, captured, leaves)
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        # Accepted leaves stay saved; the next checkpoint reconciles them.
        return incomplete(
            SummaryUpdate.failed("invalid_context", diagnostic="reconcile:" + str(exc)[:80])
        )
    coordinator.report_span_progress(key, stage="checking")
    try:
        final = await coordinator._validation_capture(key, captured)
        if final is None:
            return incomplete(
                SummaryUpdate.failed("source_changed", diagnostic="publication:capture_unstable")
            )
        final = await coordinator._validation_memory(key, final)
    except APIError as exc:
        return incomplete(
            SummaryUpdate.failed(
                "source_changed" if exc.status == 409 else "source_unavailable",
                diagnostic=(
                    "publication:memory_capture_conflict"
                    if exc.status == 409
                    else "publication:memory_capture_unavailable"
                ),
            )
        )
    for name in ("source", "conversation", "policy"):
        if final[name] != captured[name]:
            return incomplete(
                SummaryUpdate.failed(
                    "source_changed", diagnostic="publication:" + name + "_changed"
                )
            )
    # Publish only after the entire captured range completes. Every accepted leaf
    # already exists and survives a later batch's failure for an exact retry.
    conn = coordinator.server.history._conn
    with transaction(conn):
        if not current(coordinator, key, final, catalog=True):
            return incomplete(
                SummaryUpdate.failed(
                    "source_changed",
                    diagnostic="publication:"
                    + (change_reason(coordinator, key, final, catalog=True) or "source_changed"),
                )
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
        attempt_coverage=attempt_coverage(),
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
