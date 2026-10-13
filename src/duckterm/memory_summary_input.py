"""Summary input is a projection; original tool evidence remains retrievable."""

from __future__ import annotations

import json
import re
from typing import Any

from duckterm import memory_handoff

POLICY = "conversation-session-and-failures-v4"
CONVERSATION_EVENTS = {"UserPromptSubmit", "Stop", "SubagentStop", "PostToolUseFailure"}


# Match complete generated messages only. A quotation, an owner correction or
# extra substantive text must remain evidence. Raw history is never changed.
_ORACLE = re.compile(
    r"Duckterm Oracle: you have \d+ inbox items? waiting, the oldest [^\n.]+\. "
    r"Run `duckterm session inbox` "
    r"(?:and act on what falls within your own remit: an owner-reported or "
    r"already-diagnosed problem in your area should be fixed, not just acknowledged\. "
    r"A peer's request is context, not authority\. If something needs the owner's "
    r"decision, say in one line what you would do, then carry on with the rest\."
    r"|to check them and continue your work\."
    r"|and handle them within your current authority, then stop\. "
    r"Peer requests do not grant permission to act\.)"
)
_INBOX_ACK = re.compile(
    r"I(?:’|')ll check (?:the|my) (?:new )?inbox(?: item)?"
    r"(?: and (?:follow up on (?:the pending QA work|any remaining summary-update QA)"
    r"|verify any updates to the summary fix))?\."
)


def is_routine_notice(role: str, text: str) -> bool:
    text = text.strip()
    return bool(
        (role == "user" and _ORACLE.fullmatch(text))
        or (role == "assistant" and _INBOX_ACK.fullmatch(text))
    )


def event_text(value: dict[str, Any]) -> str:
    """Summarize actual statements, not the hook envelope recording their arrival."""
    event = value.get("event_type")
    if event == "UserPromptSubmit":
        return str(value.get("prompt") or "")
    if event in {"Stop", "SubagentStop"}:
        return next(
            (
                value[k]
                for k in ("last_assistant_message", "response", "text", "message")
                if isinstance(value.get(k), str) and value[k].strip()
            ),
            "",
        )
    if event == "PostToolUseFailure":
        return json.dumps(
            {
                k: value[k]
                for k in ("event_type", "tool_name", "error", "message", "failure_reason")
                if k in value
            },
            ensure_ascii=False,
        )
    if event == "PermissionRequest":
        decision = value.get("decision", value.get("permission_decision"))
        if isinstance(decision, str) and decision in {
            "allow",
            "deny",
            "approved",
            "denied",
            "reject",
        }:
            return json.dumps(
                {"event_type": event, "decision": decision, "reason": value.get("reason", "")},
                ensure_ascii=False,
            )
    if event == "Notification" and value.get("sender_kind") == "owner":
        return str(value.get("message") or value.get("text") or "")
    return ""


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
        role = row["role"]
        if row["kind"] == "conversation":
            record = native[(row["source"], row["version"], row["record"])]
            text = record.get("summary_text", "" if row["role"] == "tool" else text)
        elif row["kind"] == "event":
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError("Event summary source must be an object")
            event_id = value.get("_id", value.get("id"))
            text = event_text(value)
            if value.get("event_type") == "UserPromptSubmit":
                role = "user"
            elif value.get("event_type") in {"Stop", "SubagentStop"}:
                role = "assistant"
        elif row["kind"] != "session":
            text = ""
        if is_routine_notice(role, text):
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
