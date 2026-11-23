"""Per-agent memory health for Settings > Checkpointing > Agent memory (owner, 2026-11-16).

Read-only. Agents active in the last 7 days, problems first:
failing (the last checkpoint attempt failed), no memory yet (never a good
checkpoint), behind (new messages and memory older than 30 minutes, the owner's
freshness target), current otherwise.
"""

from __future__ import annotations

import json
from typing import Any

from duckterm import memory_checkpoint
from duckterm.core.auto_checkpoints import ACTIVE_MS, MESSAGE_EVENTS

FRESH_MS = 30 * 60 * 1000
ORDER = ("failing", "none", "behind", "current")


def agent(conn: Any, row: Any, now: int) -> dict[str, Any]:
    attempts = conn.execute(
        "SELECT created_at, label, record_json FROM checkpoints WHERE session_key=? "
        "ORDER BY created_at DESC, rowid DESC",
        (row["session_key"],),
    ).fetchall()
    last = None
    saved_at = None
    gaps = 0
    for created_at, label, text in attempts:
        if label == memory_checkpoint.GAP_LABEL:
            gaps += 1
            continue
        update = json.loads(text).get("summary_update") or {}
        if last is None:
            last = {"at": created_at, "state": update.get("state"), "reason": update.get("reason")}
        if saved_at is None and update.get("state") in {"updated", "reused"}:
            saved_at = created_at
    since = attempts[0][0] if attempts else row["started_at"] or 0
    new = conn.execute(
        "SELECT count(*) FROM events WHERE session_key=? AND ts>? "
        f"AND event_type IN ({','.join('?' * len(MESSAGE_EVENTS))})",
        (row["session_key"], since, *MESSAGE_EVENTS),
    ).fetchone()[0]
    if last and last["state"] in {"failed", "partial"}:
        status = "failing"
    elif saved_at is None:
        status = "none"
    elif new and now - saved_at > FRESH_MS:
        status = "behind"
    else:
        status = "current"
    return {
        "key": row["session_key"],
        "name": row["name"] or row["session_key"][:8],
        "runtime": row["runtime"],
        "status": status,
        "saved_at": saved_at,
        "new_messages": new,
        "last_attempt": last,
        "gaps": gaps,
        "active_at": row["updated_at"],
    }


def report(conn: Any, now: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT session_key, name, runtime, started_at, updated_at FROM sessions "
        "WHERE test=0 AND merged_into IS NULL AND updated_at>? ORDER BY updated_at DESC",
        (now - ACTIVE_MS,),
    ).fetchall()
    agents = [agent(conn, row, now) for row in rows]
    return sorted(agents, key=lambda a: ORDER.index(a["status"]))
