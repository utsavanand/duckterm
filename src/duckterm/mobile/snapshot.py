"""Bounded phone projections never include transcripts, paths, commands or credentials."""

from typing import TYPE_CHECKING, Any

from duckterm.core import events
from duckterm.runtimes.base import AT_REST_STATES

if TYPE_CHECKING:
    from duckterm.server import Server

LIMIT = 1000


def snapshot(
    server: "Server",
    host_id: str,
    now: int,
    cursor: str | None = None,
    *,
    initial_cursor: str | None = None,
) -> dict[str, Any]:
    rows = [row for row in server.history.sessions() if row["state"] not in AT_REST_STATES]
    rows.sort(
        key=lambda row: (
            row["state"] != "waiting",
            -int(row.get("updated_at") or 0),
            row["session_key"],
        )
    )
    sessions = [
        {
            "id": row["session_key"],
            "name": row.get("name") or row.get("source_app") or row["session_key"],
            "folder": row.get("grp") or "",
            "runtime": row.get("runtime") or "generic",
            "state": row["state"],
            "observed_at": row["updated_at"],
            "approval_visibility": (
                "terminal_only" if row.get("runtime") in {"codex", "generic", None} else "observed"
            ),
        }
        for row in rows[:LIMIT]
    ]
    recent = server.bus.recent(500)
    position = next((i for i, event in enumerate(recent) if event["_id"] == cursor), None)
    # An empty ring still needs a cursor: otherwise the first later event is
    # mistaken for history from before pairing. The marker belongs to this
    # bridge lifetime; a full ring means it may have overrun and must reset.
    if initial_cursor is not None and cursor == initial_cursor and len(recent) < 500:
        position = -1
    reset = cursor is not None and position is None
    live = {row["session_key"] for row in rows}
    notices = []
    # Initial pairing, lost cursors and restarts take a fresh snapshot, not a
    # burst of historical notifications. Exact event IDs deduplicate retries.
    for event in recent[position + 1 :] if position is not None else []:
        key = event.get("session_key")
        if key not in live or event.get("agent_id"):
            continue
        kind = None
        if (
            event.get("event_type") == events.NOTIFICATION
            and event.get("notification_type") == "permission_prompt"
        ):
            kind = "attention"
        elif (
            event.get("event_type") == events.STOP
            and event.get("hook_event") is True
            and event.get("stop_hook_active") is not True
        ):
            kind = "turn_finished"
        if kind:
            notices.append(
                {"id": event["_id"], "session_id": key, "kind": kind, "at": event["_ts"]}
            )
    folders = server.history.folders()
    return {
        "version": 1,
        "host_id": host_id,
        "generated_at": now,
        "read_only": True,
        "sessions": sessions,
        "folders": folders[:LIMIT],
        "truncated": len(rows) > LIMIT or len(folders) > LIMIT,
        "cursor": recent[-1]["_id"] if recent else initial_cursor,
        "reset": reset,
        "notices": notices,
        "notification_delivery": "foreground_refresh",
    }
