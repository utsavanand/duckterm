"""Read-only checkpoint planning over existing originals and accepted leaf coverage."""

from __future__ import annotations

from typing import Any

from duckterm import checkpoint_settings, memory_continuity, memory_spans
from duckterm.core import memory_folding
from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import fingerprint


def choice(value: Any) -> int | str:
    if value == "all":
        return "all"
    if type(value) is not int or not 1 <= value <= 1000:
        raise APIError(400, "Choose 1 to 1000 records or all")
    return value


def validate_plan(value: Any) -> dict[str, Any]:
    fields = {"version", "boundary", "conversation", "covered_hash", "settings_hash"}
    if (
        not isinstance(value, dict)
        or set(value) != fields | ({"order"} if value.get("version") == 2 else set())
        or type(value["version"]) is not int
        or value["version"] not in {1, 2}
        or (
            value["version"] == 2
            and (
                not isinstance(value["order"], str)
                or value["order"] not in {"oldest_unprocessed_first", "newest_unprocessed_first"}
            )
        )
        or not isinstance(value["boundary"], dict)
        or any(
            not isinstance(value[k], str) for k in ("conversation", "covered_hash", "settings_hash")
        )
    ):
        raise APIError(400, "Invalid checkpoint selection plan")
    return value


def coverage(server: Any, key: str, captured: dict[str, Any]) -> dict[tuple[str, str], str]:
    saved = memory_folding.rows(server.digests, key, captured["source"]["scope_hash"])
    return memory_spans.covered_records(saved)


def _pending(
    rows: list[dict[str, Any]], covered: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if r["kind"] == "conversation"
        and not r["reference_only"]
        and covered.get((r["source"], r["record"])) != r["hash"]
    ]


def _covered_hash(rows: list[dict[str, Any]], covered: dict[tuple[str, str], str]) -> str:
    return fingerprint(
        [
            [r["source"], r["record"], r["hash"]]
            for r in rows
            if covered.get((r["source"], r["record"])) == r["hash"]
        ]
    )


async def preview(server: Any, key: str) -> dict[str, Any]:
    if server.history.session(key) is None:
        raise APIError(404, "Session not found")
    captured = await server.progress_coordinator.capture(key)
    if captured is None:
        raise APIError(409, "History changed while counting records; refresh the checkpoint dialog")
    captured = await memory_continuity.capture(server, key, captured)
    if captured.get("memory_gaps") or "memory_sources" not in captured:
        raise APIError(409, "Conversation history is unavailable for checkpoint planning")
    settings = checkpoint_settings.load()
    rows = memory_spans.original_rows(captured)
    covered = coverage(server, key, captured)
    pending = _pending(rows, covered)
    available = sum(r["kind"] == "conversation" and not r["reference_only"] for r in rows)
    lookback = settings["lookback_records"]
    selected = len(pending) if lookback == "all" else min(lookback, len(pending))
    return {
        "available_records": available,
        "processed_records": available - len(pending),
        "pending_records": len(pending),
        "default_records": settings["lookback_records"],
        "selected_records": selected,
        "remaining_records": len(pending) - selected,
        "order": "newest_unprocessed_first",
        "selection_plan": {
            "version": 2,
            "order": "newest_unprocessed_first",
            "boundary": memory_spans.boundary(captured),
            "conversation": fingerprint(captured["conversation"]),
            "covered_hash": _covered_hash(rows, covered),
            "settings_hash": fingerprint(settings),
        },
    }


def selected(
    server: Any,
    key: str,
    captured: dict[str, Any],
    settings: dict[str, Any],
    selection: dict[str, Any],
    *,
    order: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    plan = validate_plan(selection.get("plan"))
    order = selection_order(plan, order)
    count = choice(selection.get("records"))
    if plan["conversation"] != fingerprint(captured["conversation"]) or plan[
        "settings_hash"
    ] != fingerprint(settings):
        raise APIError(409, "Session or checkpoint settings changed; refresh the checkpoint dialog")
    try:
        rows = memory_spans.rows_through(captured, plan["boundary"])
    except (ValueError, TypeError, KeyError) as exc:
        raise APIError(409, "Selected history changed; refresh the checkpoint dialog") from exc
    covered = coverage(server, key, captured)
    if plan["covered_hash"] != _covered_hash(rows, covered):
        raise APIError(409, "Checkpoint coverage changed; refresh the checkpoint dialog")
    pending = _pending(rows, covered)
    if not pending:
        raise APIError(409, "No unprocessed records remain in this selection")
    if order not in {"oldest_unprocessed_first", "newest_unprocessed_first"}:
        raise APIError(400, "Invalid record selection order")
    if order == "newest_unprocessed_first":
        # A current conversation may already occupy an earlier catalog slot
        # from a retained revision. Dict insertion order is not its recency.
        pending.sort(key=lambda row: bool(row["current"]))
    chosen = (
        pending
        if count == "all"
        else (
            pending[-int(count) :] if order == "newest_unprocessed_first" else pending[: int(count)]
        )
    )
    return chosen, sum(r["kind"] == "conversation" and not r["reference_only"] for r in rows)


def selection_order(plan: dict[str, Any], override: str | None = None) -> str:
    """An existing confirmed plan keeps its meaning across an application upgrade."""
    if plan["version"] == 2:
        if override is not None and override != plan["order"]:
            raise APIError(409, "Record selection order changed; refresh the checkpoint dialog")
        return str(plan["order"])
    # v1 manual dialogs promised oldest first; legacy harness preparation explicitly
    # selected newest. Preserve both contracts instead of reinterpreting old plans.
    return override or "oldest_unprocessed_first"
