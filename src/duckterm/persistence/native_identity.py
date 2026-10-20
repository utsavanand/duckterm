"""Durable native identity in existing restart metadata; no schema migration."""

import json
import re
import sqlite3
from typing import Any


def valid(value: Any) -> str | None:
    return (
        value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) else None
    )


def legacy(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT json_extract(payload_json,'$.session_id') AS sid FROM events "
        "WHERE session_key=? AND json_valid(payload_json) AND sid IS NOT NULL "
        "ORDER BY ts DESC, rowid DESC LIMIT 1",
        (key,),
    ).fetchone()
    return valid(row["sid"]) if row else None


def info(row: dict[str, Any], legacy_id: str | None = None) -> dict[str, Any]:
    control = json.loads(row.get("restart_json") or "{}")
    binding = control.get("native_binding")
    if binding is not None:
        source = binding.get("source", "observed")
        if binding.get("source") == "detached" and binding.get("native_id") is None:
            return {"native_id": None, "source": "none", "status": "missing"}
        if binding.get("contested"):
            return {"native_id": None, "source": source, "status": "contested"}
        # An explicit empty binding is a generation boundary, never permission
        # to rediscover the previous conversation from an observation or event.
        if binding.get("runtime") != row.get("runtime") or not valid(binding.get("native_id")):
            return {"native_id": None, "source": "none", "status": "pending"}
        return {"native_id": binding["native_id"], "source": source, "status": "recorded"}
    observed = control.get("native_observation") or {}
    native_id = (
        valid(observed.get("native_id")) if observed.get("runtime") == row.get("runtime") else None
    )
    native_id = native_id or legacy_id
    return {
        "native_id": native_id,
        "source": "observed" if native_id else "none",
        "status": "recorded" if native_id else "missing",
    }


def record(conn: sqlite3.Connection, key: str, event: dict[str, Any]) -> None:
    row = conn.execute("SELECT * FROM sessions WHERE session_key=?", (key,)).fetchone()
    if row is None:
        return
    control = json.loads(row["restart_json"] or "{}")
    assigned = valid(event.get("_assigned_native_id"))
    if assigned:
        others = conn.execute(
            "SELECT * FROM sessions WHERE runtime=? AND session_key!=?", (event["runtime"], key)
        ).fetchall()
        for other in others:
            binding = json.loads(other["restart_json"] or "{}").get("native_binding") or {}
            identity = info(dict(other))
            if identity["status"] == "missing":
                identity = info(dict(other), legacy(conn, other["session_key"]))
            if binding.get("native_id") == assigned or identity["native_id"] == assigned:
                raise ValueError("This conversation ID already belongs to another DuckTerm session")
        previous = control.get("native_binding") or {}
        control["native_binding"] = {
            "runtime": event["runtime"],
            "native_id": assigned,
            "source": "assigned",
            "generation": event["launch_generation"],
            "retired_ids": previous.get("retired_ids", []),
        }
        control["native_id_pending"] = False
        control.pop("native_observation", None)
    else:
        observed = valid(event.get("session_id"))
        if control.get("native_binding") is not None or not observed:
            return
        previous = control.get("native_observation") or {}
        if previous.get("runtime") == row["runtime"] and previous.get("ts", 0) > event["_ts"]:
            return
        control["native_observation"] = {
            "runtime": row["runtime"],
            "native_id": observed,
            "ts": event["_ts"],
        }
    conn.execute(
        "UPDATE sessions SET restart_json=? WHERE session_key=?", (json.dumps(control), key)
    )


def revision(row: dict[str, Any]) -> str:
    """Identity and lifecycle snapshot; unrelated display-name edits are safe."""
    import hashlib

    fields = ("state", "runtime", "cwd", "worktree_path", "launched", "updated_at", "restart_json")
    raw = json.dumps({field: row.get(field) for field in fields}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def adopt(conn: sqlite3.Connection, key: str, expected: str, native_id: str) -> None:
    """Serialize the stale/duplicate checks and durable attachment across servers."""
    import uuid

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        saved = conn.execute("SELECT * FROM sessions WHERE session_key=?", (key,)).fetchone()
        if saved is None or revision(dict(saved)) != expected:
            raise ValueError("Session changed; reload the conversation picker")
        row = dict(saved)
        current = info(row, legacy(conn, key))
        if row.get("state") not in {"stopped", "interrupted"} or current["status"] != "missing":
            raise ValueError("This session is no longer eligible for recovery")
        for peer in conn.execute("SELECT * FROM sessions WHERE runtime=?", (row["runtime"],)):
            if peer["session_key"] == key:
                continue
            control = json.loads(peer["restart_json"] or "{}")
            binding = control.get("native_binding") or {}
            identity = info(dict(peer), legacy(conn, peer["session_key"]))
            if binding.get("native_id") == native_id or identity["native_id"] == native_id:
                raise ValueError("This conversation already belongs to another DuckTerm session")
        control = json.loads(row.get("restart_json") or "{}")
        control["native_binding"] = {
            "native_id": native_id,
            "runtime": row["runtime"],
            "source": "adopted",
            "generation": uuid.uuid4().hex,
            "retired_ids": [],
        }
        control["native_id_pending"] = False
        control.pop("native_observation", None)
        conn.execute(
            "UPDATE sessions SET restart_json=? WHERE session_key=?", (json.dumps(control), key)
        )


def detach(conn: sqlite3.Connection, key: str, expected: str) -> None:
    """Undo only an explicit adoption; keep a generation barrier to old hooks."""
    import time
    import uuid

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        saved = conn.execute("SELECT * FROM sessions WHERE session_key=?", (key,)).fetchone()
        if saved is None or revision(dict(saved)) != expected:
            raise ValueError("Session changed; check recovery status before undoing")
        row = dict(saved)
        control = json.loads(row.get("restart_json") or "{}")
        binding = control.get("native_binding") or {}
        if (
            row.get("state") not in {"stopped", "interrupted", "terminated"}
            or binding.get("source") != "adopted"
        ):
            raise ValueError("Only a stopped session's explicitly adopted identity can be undone")
        control["native_detach"] = {
            "native_id": binding.get("native_id"),
            "at": int(time.time() * 1000),
        }
        control["native_binding"] = {
            "runtime": row["runtime"],
            "source": "detached",
            "native_id": None,
            "generation": uuid.uuid4().hex,
            "retired_ids": [binding["native_id"]],
        }
        control.pop("native_observation", None)
        control["native_id_pending"] = False
        conn.execute(
            "UPDATE sessions SET restart_json=? WHERE session_key=?", (json.dumps(control), key)
        )
