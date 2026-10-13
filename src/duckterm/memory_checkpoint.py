"""A manual checkpoint summarizes a bounded snapshot, not a moving live backlog."""

from __future__ import annotations

import asyncio
from typing import Any

from duckterm import memory_continuity, memory_sources, memory_spans
from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import conversation


async def freeze(
    server: Any,
    key: str,
    captured: dict[str, Any],
    settings: dict[str, Any],
    *,
    selection: dict[str, Any] | None = None,
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
            source = {**source, "snapshot": saved["snapshot"]}
        sources.append(source)
    captured = {**captured, "memory_sources": sources, "checkpoint_settings": dict(settings)}
    # Native messages contain conversation and failure text; hook copies, cards,
    # tasks and mail remain retrievable, without crowding recent conversation out.
    rows = memory_spans.original_rows(captured)
    eligible = [r for r in rows if r["kind"] == "conversation" and not r["reference_only"]]
    eligible.sort(key=lambda r: bool(r["current"]))  # old generations before the current one
    chosen = eligible[-settings["lookback_records"] :]
    available = len(eligible)
    if selection is not None:
        from duckterm import checkpoint_plan

        chosen, available = checkpoint_plan.selected(server, key, captured, settings, selection)
        captured["checkpoint_order"] = "oldest_unprocessed_first"
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
