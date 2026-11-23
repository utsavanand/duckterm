"""A manual checkpoint summarizes a bounded snapshot, not a moving live backlog."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from duckterm import checkpoint_settings, memory_continuity, memory_sources, memory_spans
from duckterm.core import memory_folding
from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import checkpoint_marker, conversation, transaction

AUTOMATIC_LABEL = "Automatic"
GAP_LABEL = "Automatic checkpoints off"


def in_gap(row: dict[str, Any], periods: list[tuple[int, int]]) -> bool:
    """Written while automatic checkpoints were off; untimed rows never are."""
    at = row.get("at")
    return isinstance(at, int) and any(off <= at <= on for off, on in periods)


def _gap(
    server: Any,
    key: str,
    captured: dict[str, Any],
    rows: list[dict[str, Any]],
    periods: list[tuple[int, int]],
) -> dict[str, Any] | None:
    """Unsummarized rows an automatic checkpoint leaves out (owner, 2026-11-15)."""
    processed: set[tuple[str, str]] = set()
    for leaf in memory_folding.rows(server.digests, key, captured["source"]["scope_hash"]):
        for source, records in leaf.get("continuity", {}).get("processed", {}).items():
            processed.update((source, record) for record in records)
    skipped = [r for r in rows if (r["source"], r["record"]) not in processed]
    if not skipped:
        return None
    return {
        "periods": [
            list(p)
            for p in sorted(
                {next((off, on) for off, on in periods if off <= r["at"] <= on) for r in skipped}
            )
        ],
        "records": len(skipped),
        "first_at": min(r["at"] for r in skipped),
        "last_at": max(r["at"] for r in skipped),
    }


def record_gap(server: Any, key: str, captured: dict[str, Any]) -> str | None:
    """Save the gap as its own checkpoint entry, once per off period per agent."""
    gap = captured.get("checkpoint_gap")
    if not gap:
        return None
    conn = server.history._conn
    seen: set[tuple[int, ...]] = set()
    for (text,) in conn.execute(
        "SELECT record_json FROM checkpoints WHERE session_key=? AND label=?", (key, GAP_LABEL)
    ):
        seen.update(tuple(p) for p in json.loads(text).get("gap", {}).get("periods", []))
    new = [p for p in gap["periods"] if tuple(p) not in seen]
    if not new:
        return None
    now = int(time.time() * 1000)
    record = checkpoint_marker(captured, None, {}, now)
    record["gap"] = {**gap, "periods": new}
    identity = uuid.uuid4().hex
    with transaction(conn):
        conn.execute(
            "INSERT INTO checkpoints "
            "(id,session_key,label,summary,record_json,markdown_path,created_at) "
            "VALUES (?,?,?,'',?,NULL,?)",
            (identity, key, GAP_LABEL, json.dumps(record), now),
        )
    return identity


async def freeze(
    server: Any,
    key: str,
    captured: dict[str, Any],
    settings: dict[str, Any],
    *,
    selection: dict[str, Any] | None = None,
    order: str | None = None,
    automatic: bool = False,
) -> dict[str, Any]:
    captured = await memory_continuity.capture(server, key, captured)
    if selection is not None and (captured.get("memory_gaps") or "memory_sources" not in captured):
        raise APIError(409, "Selected history is unavailable; refresh the checkpoint dialog")
    if "memory_sources" not in captured:
        return captured
    sources = []
    for source in captured["memory_sources"]:
        if source["kind"] == "conversation":
            saved = await asyncio.to_thread(memory_sources.read_native, key, source, retain=True)
            if saved["version"] != source["version"]:
                raise APIError(409, "Conversation changed while capturing checkpoint")
            source = {
                **source,
                "snapshot": saved["snapshot"],
                "records_sha256": saved["records_sha256"],
            }
        sources.append(source)
    captured = {**captured, "memory_sources": sources, "checkpoint_settings": dict(settings)}
    # Native messages contain conversation and failure text; hook copies, cards,
    # tasks and mail remain retrievable, without crowding recent conversation out.
    rows = memory_spans.original_rows(captured)
    eligible = [r for r in rows if r["kind"] == "conversation" and not r["reference_only"]]
    eligible.sort(key=lambda r: bool(r["current"]))  # old generations before the current one
    lookback = settings["lookback_records"]
    chosen = eligible if lookback == "all" else eligible[-lookback:]
    if automatic:
        periods = checkpoint_settings.off_periods(settings)
        skipped = [r for r in chosen if in_gap(r, periods)]
        if skipped:
            chosen = [r for r in chosen if not in_gap(r, periods)]
            gap = _gap(server, key, captured, skipped, periods)
            if gap:
                captured["checkpoint_gap"] = gap
    available = len(eligible)
    if selection is not None:
        from duckterm import checkpoint_plan

        order = checkpoint_plan.selection_order(
            checkpoint_plan.validate_plan(selection.get("plan")), order
        )
        chosen, available = checkpoint_plan.selected(
            server, key, captured, settings, selection, order=order
        )
        captured["checkpoint_order"] = order
    captured["checkpoint_selection"] = {(r["source"], r["record"]): r["hash"] for r in chosen}
    captured["checkpoint_available"] = available
    return captured


def changed(server: Any, key: str, captured: dict[str, Any]) -> str | None:
    """Keep identity and sharing boundaries; ordinary activity is not invalidation."""
    from duckterm.core.saved_progress import policy_key

    row = server.history.session(key)
    if row is None or conversation(server.history, key, row) != captured["conversation"]:
        return "conversation_changed"
    if policy_key() != captured["policy"]:
        return "policy_changed"
    old = captured["facts"]["required"]["scope"]
    member = server.history._conn.execute(
        "SELECT root,folder FROM session_api_members WHERE session_key=?", (key,)
    ).fetchone()
    scope = dict(member) if member else {}
    if any(scope.get(k) != old.get(k) for k in ("root", "folder")) or (
        scope.get("folder") and scope["folder"] != (row.get("grp") or "")
    ):
        return "scope_changed"
    return None
