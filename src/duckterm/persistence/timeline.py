"""Read-only timeline projection. No generated history or background work."""

import base64
import json
import sqlite3
import time
from typing import Any

KINDS = (
    "prompt",
    "delivered",
    "learned",
    "next_action",
    "completed",
    "artifact",
    "decision",
    "checkpoint",
    "restart",
    "model",
    "harness",
    "needs-you",
)


def _sources() -> list[tuple[str, str, str, str, str, str]]:
    # kind, table, scope column, timestamp, predicate, detail JSON expression.
    sources = [
        (
            "prompt",
            "events",
            "session_key",
            "ts",
            "event_type='UserPromptSubmit'",
            "json_object('text', substr(json_extract(payload_json,'$.prompt'),1,16000))",
        ),
        (
            "checkpoint",
            "checkpoints",
            "session_key",
            "created_at",
            "1",
            "json_object('text',substr(label,1,1000),'summary',substr(summary,1,16000))",
        ),
        (
            "artifact",
            "artifacts",
            "session_key",
            "created_at",
            "1",
            "json_object('text',title,'media_type',media_type,'removed',json('false'))",
        ),
        (
            "artifact",
            "artifact_metadata",
            "session_key",
            "json_extract(snapshot_json,'$.created_at')",
            "removed_at IS NOT NULL",
            "json_object('text',json_extract(snapshot_json,'$.title'),'removed',json('true'),"
            "'removed_at',removed_at)",
        ),
        (
            "decision",
            "session_questions",
            "recipient",
            "answered_at",
            "sender='owner' AND status='answered' AND answered_at IS NOT NULL",
            "json_object('text',substr(question,1,16000),'answer',substr(answer,1,16000))",
        ),
    ]
    for kind, bucket in [
        ("delivered", "deliverables"),
        ("learned", "learnings"),
        ("learned", "user_learnings"),
        ("next_action", "next_actions"),
    ]:
        sources.append(
            (
                kind,
                "digest_items",
                "session_key",
                "created_at",
                f"bucket='{bucket}'",
                "json_object('text',substr(text,1,16000),'bucket',bucket)",
            )
        )
    sources.append(
        (
            "completed",
            "digest_items",
            "session_key",
            "updated_at",
            "bucket='next_actions' AND status='done'",
            "json_object('text',substr(text,1,16000),'bucket',bucket)",
        )
    )
    for kind, event in [
        ("restart", "RestartCompleted"),
        ("model", "ModelChanged"),
        ("harness", "HarnessSwitched"),
    ]:
        sources.append(
            (
                kind,
                "events",
                "session_key",
                "ts",
                f"event_type='{event}'",
                "json_object('text',event_type,'from_harness',"
                "json_extract(payload_json,'$.from_harness'),'to_harness',"
                "json_extract(payload_json,'$.to_harness'),'model',"
                "json_extract(payload_json,'$.model'))",
            )
        )
    return sources


def _decode(value: str) -> dict[str, Any]:
    try:
        if len(value) > 32768:
            raise ValueError
        result = json.loads(base64.urlsafe_b64decode(value.encode()))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid timeline cursor") from exc


def _encode(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def page(
    history: sqlite3.Connection,
    digests: sqlite3.Connection,
    row: dict[str, Any],
    notes: list[dict[str, Any]],
    *,
    before: str = "",
    limit: int = 50,
    kinds: str = "",
    now: int | None = None,
) -> dict[str, Any]:
    """Call on the stores' owner thread; each source returns at most limit+1 rows.

    High-water rowids exclude concurrent inserts, even backdated/tied timestamps.
    Existing mutable records remain live; this is not a historical DB snapshot.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    selected = sorted(set(kinds.split(","))) if kinds else sorted(KINDS)
    if set(selected) - set(KINDS):
        raise ValueError("Unknown timeline kind")
    key = row["session_key"]
    stamp = int(time.time() * 1000) if now is None else now
    connections = {"history": history, "digests": digests}
    tables = {
        name: {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name, conn in connections.items()
    }
    sources = _sources()
    marks: dict[str, int] = {}
    anchors: dict[str, str | None] = {}
    for _, table, _, _, _, _ in sources:
        db = "digests" if table == "digest_items" else "history"
        if table in tables[db] and table not in marks:
            anchor = (
                connections[db]
                .execute(f"SELECT rowid, id FROM {table} ORDER BY rowid DESC LIMIT 1")
                .fetchone()
            )
            marks[table] = anchor[0] if anchor else 0
            anchors[table] = str(anchor[1]) if anchor else None
    snapshot = {
        "v": 2,
        "key": key,
        "kinds": selected,
        "at": stamp,
        "marks": marks,
        "anchors": anchors,
        "notes": [str(n["id"]) for n in notes if n.get("session_key") == key and n.get("id")],
    }
    position = None
    if before:
        cursor = _decode(before)
        try:
            pos = cursor["position"]
            valid = (
                cursor["v"] == 2
                and cursor["key"] == key
                and cursor["kinds"] == selected
                and type(cursor["at"]) is int
                and 0 <= cursor["at"] <= stamp
                and isinstance(cursor["marks"], dict)
                and set(cursor["marks"]) <= set(marks)
                and all(type(v) is int and 0 <= v <= 2**63 - 1 for v in cursor["marks"].values())
                and isinstance(cursor["anchors"], dict)
                and set(cursor["anchors"]) == set(cursor["marks"])
                and all(v is None or isinstance(v, str) for v in cursor["anchors"].values())
                and isinstance(cursor["notes"], list)
                and len(cursor["notes"]) <= 500
                and all(isinstance(v, str) for v in cursor["notes"])
                and isinstance(pos, list)
                and len(pos) == 2
                and type(pos[0]) is int
                and 0 <= pos[0] <= cursor["at"]
                and isinstance(pos[1], str)
            )
            if not valid:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid timeline cursor or changed filters") from exc
        # SQLite may reuse a retired maximum rowid. A retained anchor proves
        # that later inserts still sort above this cursor's rowid boundary.
        for table, mark in cursor["marks"].items():
            if not mark:
                continue
            conn = digests if table == "digest_items" else history
            anchor = conn.execute(f"SELECT id FROM {table} WHERE rowid=?", (mark,)).fetchone()
            if anchor is None or str(anchor[0]) != cursor["anchors"][table]:
                raise ValueError("Invalid timeline cursor: source boundary changed; refresh")
        snapshot = cursor
        position = (pos[0], pos[1])
    entries = []
    counts = {kind: 0 for kind in selected}
    for kind, table, scope, ts, predicate, detail in sources:
        if kind not in selected or table not in snapshot["marks"]:
            continue
        conn = digests if table == "digest_items" else history
        sql = (
            f"SELECT '{kind}:' || id AS entry_id, id AS source_id, {ts} AS ts, {detail} AS detail "
            f"FROM {table} WHERE {scope}=? AND rowid<=? AND ({predicate})"
        )
        args: list[Any] = [key, snapshot["marks"][table], snapshot["at"]]
        query = f"SELECT * FROM ({sql}) WHERE ts IS NOT NULL AND ts<=?"
        counts[kind] += conn.execute(f"SELECT COUNT(*) FROM ({query})", args).fetchone()[0]
        if position:
            query += " AND (ts<? OR (ts=? AND entry_id<?))"
            args.extend([position[0], position[0], position[1]])
        for record in conn.execute(
            query + " ORDER BY ts DESC, entry_id DESC LIMIT ?", [*args, limit + 1]
        ):
            data = json.loads(record["detail"])
            entries.append(
                _entry(record["entry_id"], record["ts"], kind, data, table, record["source_id"])
            )
    if "needs-you" in selected:
        for note in notes:
            if (
                note.get("session_key") != key
                or str(note.get("id")) not in snapshot["notes"]
                or note.get("urgency") == "offer"
                or note.get("blocking") is False
            ):
                continue
            note_ts = note.get("created_at")
            if not isinstance(note_ts, int) or note_ts > snapshot["at"]:
                continue
            counts["needs-you"] += 1
            identity = "needs-you:" + str(note["id"])
            if position and (note_ts, identity) >= position:
                continue
            closed = note.get("closed_at")
            data = {
                "text": str(note.get("question") or note.get("detail") or "Needs your attention")[
                    :16000
                ],
                "status": note.get("status"),
                "closed_at": closed,
                "duration_ms": max(0, closed - note_ts) if isinstance(closed, int) else None,
            }
            entries.append(_entry(identity, note_ts, "needs-you", data, "relay", str(note["id"])))
    entries.sort(key=lambda entry: (entry["ts"], entry["id"]), reverse=True)
    more = len(entries) > limit
    entries = entries[:limit]
    next_cursor = None
    if more:
        snapshot["position"] = [entries[-1]["ts"], entries[-1]["id"]]
        next_cursor = _encode(snapshot)
    try:
        progress = json.loads(row.get("progress") or "{}")
    except (ValueError, TypeError):
        progress = {}
    summary = progress.get("summary", "") if isinstance(progress, dict) else ""
    return {
        "summary": {
            "text": summary,
            "harness": row.get("runtime"),
            "model": row.get("model"),
            "age_ms": max(0, stamp - row["started_at"]),
            "counts": counts,
            "total": sum(counts.values()),
        },
        "entries": entries,
        "next_cursor": next_cursor,
    }


def _entry(
    identity: str, ts: int, kind: str, detail: dict[str, Any], source: str, ref: str
) -> dict[str, Any]:
    return {
        "id": identity,
        "ts": ts,
        "kind": kind,
        "icon": kind,
        "one_line": " ".join(str(detail.get("text") or kind).split())[:240],
        "detail": detail,
        "refs": [{"source": source, "id": ref}],
    }
