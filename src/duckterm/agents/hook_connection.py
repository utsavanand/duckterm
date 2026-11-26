"""Hook delivery evidence for the current launch, using existing session storage."""

import json
import sqlite3
from typing import Any

from duckterm.core import events
from duckterm.runtimes.base import AT_REST_STATES


def read(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(row.get("restart_json") or "{}").get("hook_connection") or {}


def record(conn: sqlite3.Connection, key: str, event: dict[str, Any]) -> None:
    if event.get("event_type") != events.SESSION_START and not event.get("hook_event"):
        return
    row = conn.execute("SELECT restart_json FROM sessions WHERE session_key=?", (key,)).fetchone()
    if row is None:
        return
    control = json.loads(row[0] or "{}")
    connection = control.get("hook_connection") or {}
    setup = event.get("hook_setup")
    if event.get("event_type") == events.SESSION_START and not event.get("hook_event"):
        # Every supervisor launch invalidates previous delivery evidence, including
        # a switch to a harness that uses a different native observation mechanism.
        connection = {}
        if isinstance(setup, dict):
            connection = {**setup, "configured_at": event["_ts"]}
        control["hook_connection"] = connection
    elif (
        event.get("hook_event") is True
        and event.get("event_type") in events.ALL
        and connection.get("generation")
        and event.get("launch_generation") == connection["generation"]
        and event.get("runtime") == connection.get("runtime")
        and not event.get("agent_id")
        and not connection.get("received_at")
    ):
        # One accepted provider event proves the path. Terminal output and the
        # supervisor's synthetic SessionStart cannot manufacture this evidence.
        control["hook_connection"] = {**connection, "received_at": event["_ts"]}
    else:
        return
    conn.execute(
        "UPDATE sessions SET restart_json=? WHERE session_key=?", (json.dumps(control), key)
    )


def status(row: dict[str, Any], now: int) -> dict[str, Any]:
    value = read(row)
    if row.get("state") in AT_REST_STATES:
        return {"state": "inactive"}
    if not value or value.get("runtime") != row.get("runtime"):
        return {"state": "unknown"}
    binding = json.loads(row.get("restart_json") or "{}").get("native_binding") or {}
    if binding and binding.get("generation") != value.get("generation"):
        return {"state": "unknown"}
    if value.get("received_at"):
        return {"state": "connected", "receivedAt": value["received_at"]}
    return {
        "state": "awaiting" if now - value["configured_at"] < 30_000 else "unconfirmed",
        "configuredAt": value["configured_at"],
    }
