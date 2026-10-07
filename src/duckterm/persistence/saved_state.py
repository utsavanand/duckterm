"""Versioned references over existing history. No second history or memory store.

SQLite work runs on the HistoryStore owner thread. Callers acquire a write
transaction before validating sources and inserting a marker. Rendering is read-only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

SCHEMA_VERSION = 12
REVISION_BUCKET = "summary_revision_v1"
MARKER_FORMAT = "checkpoint_marker_v2"


def checkpoint_marker(
    captured: dict[str, Any], summary_ref: str | None, git: dict[str, Any], created_at: int
) -> dict[str, Any]:
    source = captured["source"]
    mail = captured["facts"]["required"]["mail"]
    return {
        "format": MARKER_FORMAT,
        "policy": captured["policy"],
        "conversation": captured["conversation"],
        "events": source["events"],
        "summary_ref": summary_ref,
        "required_hash": source["required_hash"],
        "mail_ids": [r["id"] for r in mail],
        "mail_hash": fingerprint(mail),
        "transcript": {k: v for k, v in source.items() if k.startswith("transcript_")},
        "git": git,
        "created_at": created_at,
    }


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Own the write lock before reading boundaries; never commit a caller's work."""
    if conn.in_transaction:
        raise RuntimeError("saved-state transaction requires an idle connection")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def event_source(conn: sqlite3.Connection, key: str, end: int | None = None) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT rowid, id, payload_json FROM events "
        "WHERE session_key=? AND rowid<=? ORDER BY rowid",
        (key, end if end is not None else 9223372036854775807),
    ).fetchall()
    return {
        "first": rows[0][0] if rows else 0,
        "last": rows[-1][0] if rows else 0,
        "first_id": rows[0][1] if rows else None,
        "last_id": rows[-1][1] if rows else None,
        "count": len(rows),
        "fingerprint": fingerprint([list(r) for r in rows]),
    }


def conversation(history: Any, key: str, row: dict[str, Any]) -> dict[str, Any]:
    binding = history.restart_control(key).get("native_binding") or {}
    return {
        "runtime": row.get("runtime"),
        "native_id": history.session_id_for(key),
        "generation": binding.get("generation"),
        "started_at": row.get("started_at"),
    }


def revision(conn: sqlite3.Connection, key: str, identity: str | None) -> dict[str, Any] | None:
    if not isinstance(identity, str) or not identity:
        return None
    row = conn.execute(
        "SELECT text, created_at FROM digest_items WHERE id=? AND session_key=? AND bucket=?",
        (identity, key, REVISION_BUCKET),
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
        if (
            not isinstance(value, dict)
            or value.get("format") != REVISION_BUCKET
            or not isinstance(value.get("summary"), str)
            or not isinstance(value.get("source"), dict)
            or not isinstance(value.get("conversation"), dict)
            or not isinstance(value.get("summary_validation"), dict)
        ):
            return None
        return {**value, "id": identity, "created_at": row[1]}
    except (ValueError, TypeError, KeyError):
        return None


def current_revision(row: dict[str, Any]) -> str | None:
    try:
        value = json.loads(row.get("progress") or "{}")
        ref = value.get("revision_id")
        return ref if isinstance(ref, str) else None
    except (ValueError, TypeError, AttributeError):
        return None


def retention_predicate(kind: str) -> str:
    """Internal SQL, used for BOTH analytics aggregation and normal expiry.

    Unknown/malformed markers suspend expiry for that session, conservatively.
    Explicit owner deletion never uses this predicate.
    """
    scope = (
        "c.session_key=e.session_key"
        if kind == "events"
        else ("(c.session_key=q.recipient OR c.session_key=q.sender)")
    )
    pin = (
        "e.rowid BETWEEN json_extract(c.record_json,'$.events.first') "
        "AND json_extract(c.record_json,'$.events.last')"
        if kind == "events"
        else "EXISTS (SELECT 1 FROM json_each(c.record_json,'$.mail_ids') m WHERE m.value=q.id)"
    )
    # CASE prevents SQLite from invoking JSON functions on corrupt data.
    return f"""NOT EXISTS (SELECT 1 FROM checkpoints c WHERE {scope} AND
      CASE WHEN NOT json_valid(c.record_json) THEN 1
      WHEN json_type(c.record_json) IS NOT 'object' THEN 1
      WHEN json_type(c.record_json,'$.format') IS NULL THEN 0
      WHEN json_extract(c.record_json,'$.format') = 'checkpoint_fork_merge_v1' THEN 0
      WHEN json_extract(c.record_json,'$.format') != '{MARKER_FORMAT}' THEN 1
      WHEN json_type(c.record_json,'$.events.first') IS NOT 'integer'
        OR json_type(c.record_json,'$.events.last') IS NOT 'integer'
        OR json_type(c.record_json,'$.mail_ids') IS NOT 'array' THEN 1
      ELSE ({pin}) END)"""


def resolve_checkpoint(
    conn: sqlite3.Connection, key: str, checkpoint: dict[str, Any]
) -> dict[str, Any]:
    """Owner-facing projection; peer APIs must separately enforce source scope."""
    from duckterm.persistence.checkpoints import _extract

    record = checkpoint["record"]
    if not isinstance(record, dict):
        record = {"format": "unsupported"}
    result = dict(checkpoint)
    result.update(saved=True, summary_source_at=None, handoff_eligible=False, reason_codes=[])
    if record.get("format") == "checkpoint_fork_merge_v1":
        note = conn.execute(
            "SELECT summary FROM fork_merges WHERE id=? AND parent=? AND child=?",
            (record.get("request_key"), key, record.get("from_session")),
        ).fetchone()
        result["summary"] = note[0] if note else ""
    if record.get("format") != MARKER_FORMAT:
        result.update(
            format="fork_merge" if record.get("kind") == "merge" else "legacy",
            summary_state="legacy-unverified",
            coverage={"state": "legacy", "events": record.get("event_count")},
            reason_codes=["legacy_baseline_unreviewed"],
        )
        if record.get("format") not in (None, "checkpoint_fork_merge_v1"):
            result.update(
                format="unsupported", summary_state="unavailable", reason_codes=["source_missing"]
            )
        result["record"] = {
            "prompts": [],
            "commands": [],
            "files": [],
            "tools": [],
            "event_count": 0,
            **record,
        }
        return result
    result["format"] = MARKER_FORMAT
    captured = record.get("events")
    if (
        not isinstance(captured, dict)
        or any(type(captured.get(k)) is not int for k in ("first", "last", "count"))
        or not 0 <= captured["first"] <= captured["last"]
        or not isinstance(record.get("mail_ids"), list)
        or not isinstance(record.get("transcript", {}), dict)
        or not isinstance(record.get("git", {}), dict)
    ):
        result.update(
            summary_state="unavailable",
            coverage={"state": "missing"},
            reason_codes=["source_missing"],
            record={"prompts": [], "commands": [], "files": [], "tools": [], "event_count": 0},
        )
        return result
    actual = event_source(conn, key, int(captured.get("last") or 0))
    complete = actual == captured
    summary = revision(conn, key, record.get("summary_ref"))
    state = "unavailable"
    reasons = []
    if summary:
        result["summary"] = summary.get("summary", "")
        result["summary_origin"] = (
            "owner-reviewed" if summary.get("review", {}).get("kind") == "owner" else "generated"
        )
        if result["summary_origin"] == "owner-reviewed":
            result["summary_review"] = summary["review"]
        result["summary_source_at"] = summary.get("source_at", summary["created_at"])
        state = (
            "ready" if summary.get("summary_validation", {}).get("ready") is True else "unverified"
        )
        summary_source = summary.get("source", {})
        if (
            summary.get("policy") != record.get("policy")
            or summary_source.get("events") != captured
            or summary_source.get("required_hash") != record.get("required_hash")
            or summary.get("conversation") != record.get("conversation")
            or any(summary_source.get(k) != v for k, v in (record.get("transcript") or {}).items())
        ):
            state = "stale"
        reasons.extend(summary.get("summary_validation", {}).get("reason_codes", []))
    if state != "ready":
        reasons.append("summary_" + state)
    if not complete:
        reasons.append("source_missing")
    mail = []
    for mail_id in record.get("mail_ids", []):
        item = conn.execute(
            "SELECT id,sender,question,answer,status FROM session_questions "
            "WHERE id=? AND recipient=?",
            (mail_id, key),
        ).fetchone()
        if item is None:
            reasons.append("source_missing")
        else:
            mail.append(dict(item))
    if record.get("mail_hash") != fingerprint(mail):
        reasons.append("source_changed")
    rows = conn.execute(
        "SELECT payload_json FROM events "
        "WHERE session_key=? AND rowid BETWEEN ? AND ? ORDER BY rowid",
        (key, captured.get("first", 0), captured.get("last", 0)),
    ).fetchall()
    activity = _extract([json.loads(r[0]) for r in rows])
    result["record"] = {**record, **activity, **record.get("git", {})}
    result.update(
        summary_state=state,
        coverage={
            "state": "retained" if complete else "missing",
            "events": actual["count"],
            "expected_events": captured.get("count", 0),
            "transcript": record.get("transcript"),
        },
        reason_codes=list(dict.fromkeys(reasons)),
        # This certifies the captured boundary only. Transitions recheck current sources.
        handoff_eligible=state == "ready" and not reasons,
    )
    return result
