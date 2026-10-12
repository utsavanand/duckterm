"""Bounded native reconciliation; a quiet terminal is never completion evidence."""

from typing import Any

from duckterm.runtimes.opencode_protocol import NativeClient, OpenCodeError, native_id


def lifecycle(
    identity: str,
    before: Any,
    after: Any,
    questions: Any,
    permissions: Any,
    messages: list[dict[str, Any]],
) -> str:
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise OpenCodeError("Malformed native lifecycle snapshot")
    pending: list[dict[str, Any]] = []
    for collection in (questions, permissions):
        if not isinstance(collection, list) or any(
            not isinstance(item, dict) for item in collection
        ):
            raise OpenCodeError("Malformed native request snapshot")
        for item in collection:
            native_id(item.get("sessionID"))
        pending.extend(item for item in collection if item.get("sessionID") == identity)
    if pending:
        return "waiting"
    statuses = []
    for snapshot in (before, after):
        entry = snapshot.get(identity, {"type": "idle"})
        if not isinstance(entry, dict) or entry.get("type") not in ("idle", "busy", "retry"):
            raise OpenCodeError("Unknown native lifecycle state")
        statuses.append(entry["type"])
    if any(status in {"busy", "retry"} for status in statuses):
        return "busy"
    if not messages:
        return "idle"
    users = [row for row in messages if row["role"] == "user"]
    if not users:
        return "unknown"
    user = users[-1]
    assistants = [
        row for row in messages if row["role"] == "assistant" and row.get("parent_id") == user["id"]
    ]
    if not assistants:
        return "unknown"
    final = assistants[-1]
    if final.get("error"):
        return "unknown"
    if not final.get("complete") or final.get("finish") not in {"stop", "length", "content-filter"}:
        return "unknown"
    if any(
        block.get("status") not in {"completed", "error"}
        for row in assistants
        for block in row["blocks"]
        if block["type"] == "tool_use"
    ):
        return "unknown"
    return "idle"


def snapshot(client: NativeClient, identity: str) -> tuple[list[dict[str, Any]], str]:
    before = client.request("GET", "/session/status")
    records = client.messages(identity)
    questions = client.request("GET", "/question")
    permissions = client.request("GET", "/permission")
    after = client.request("GET", "/session/status")
    return records, lifecycle(identity, before, after, questions, permissions, records)
