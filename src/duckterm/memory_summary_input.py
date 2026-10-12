"""Summary input is a projection; original tool evidence remains retrievable."""

from __future__ import annotations

import json
from typing import Any

from duckterm import memory_handoff

POLICY = "conversation-session-and-failures-v2"
CONVERSATION_EVENTS = {"UserPromptSubmit", "Stop", "SubagentStop", "PostToolUseFailure"}


def records(sources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    originals = memory_handoff.records(sources)
    native = {
        (s["id"], s["version"], r["id"]): r
        for s in sources
        if s["kind"] == "conversation"
        for r in s["records"]
    }
    projected = []
    original_bytes = eligible_bytes = 0
    for row in originals:
        original_bytes += len(row["text"].encode())
        text = row["text"]
        event_id = None
        if row["kind"] == "conversation":
            record = native[(row["source"], row["version"], row["record"])]
            text = record.get("summary_text", "" if row["role"] == "tool" else text)
        elif row["kind"] == "event":
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError("Event summary source must be an object")
            event_id = value.get("_id", value.get("id"))
            if value.get("event_type") not in CONVERSATION_EVENTS:
                text = ""
        elif row["kind"] != "session":
            text = ""
        eligible_bytes += len(text.encode())
        projected.append(
            {
                **row,
                "text": text,
                "reference_only": not bool(text),
                **({"event_id": event_id} if isinstance(event_id, str) else {}),
            }
        )
    eligible = sum(not r["reference_only"] for r in projected)
    return projected, {
        "policy": POLICY,
        "original_records": len(originals),
        "eligible_records": eligible,
        "reference_only_records": len(originals) - eligible,
        "original_text_bytes": original_bytes,
        "eligible_text_bytes": eligible_bytes,
    }
