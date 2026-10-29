"""Read explicit native context and compaction evidence without invoking an agent."""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from duckterm.harnesses import runtime_for

# A selected panel can inspect recent history without scanning an unbounded log.
# Absence within this window is unknown, never proof that compaction never ran.
TAIL_BYTES = 8 * 1024 * 1024
HEAD_BYTES = 256 * 1024


def unavailable(reason: str) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "reason": reason,
        "context_window_tokens": None,
        "context_tokens": None,
        "model": None,
        "last_compaction_at": None,
        "history_scope": None,
    }


def _objects(data: bytes) -> list[dict[str, Any]]:
    result = []
    for line in data.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value if parsed.tzinfo is not None else None
    except ValueError:
        return None


@lru_cache(maxsize=128)
def _read(
    path: Path, runtime: str, native_id: str, signature: tuple[int, int, int, int]
) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            head = stream.read(HEAD_BYTES)
            offset = max(0, signature[2] - TAIL_BYTES)
            stream.seek(offset)
            tail = stream.read(TAIL_BYTES)
        if _signature(path) != signature:
            return unavailable("transcript_changed")
    except OSError:
        return unavailable("transcript_unavailable")
    # Never parse a partial first line as a complete record.
    records = _objects(tail.split(b"\n", 1)[-1] if offset else tail)
    first = _objects(head)
    if runtime == "codex":
        meta = first[0] if first else {}
        payload = meta.get("payload")
        if (
            meta.get("type") != "session_meta"
            or not isinstance(payload, dict)
            or payload.get("id") != native_id
        ):
            return unavailable("identity_unverified")
    elif not any(row.get("sessionId") == native_id for row in first + records) or any(
        row.get("sessionId") not in (None, native_id) for row in first + records
    ):
        return unavailable("identity_unverified")
    result: dict[str, Any] = {
        "state": "available",
        "reason": None,
        "context_window_tokens": None,
        "context_tokens": None,
        "model": None,
        "last_compaction_at": None,
        "history_scope": "recent" if offset else "complete",
    }
    capacity_seen = False
    model_seen = False
    compaction_seen = False
    for row in reversed(records):
        payload = row.get("payload")
        if runtime == "codex" and not model_seen and row.get("type") == "turn_context":
            model_seen = True
            model = payload.get("model") if isinstance(payload, dict) else None
            if isinstance(model, str) and model.strip() and len(model) <= 256:
                result["model"] = model.strip()
        if not compaction_seen:
            compacted = (
                runtime == "claude-code"
                and row.get("type") == "system"
                and row.get("subtype") == "compact_boundary"
                and isinstance(row.get("uuid"), str)
                and bool(row["uuid"])
            ) or (
                runtime == "codex"
                and row.get("type") == "compacted"
                and isinstance(payload, dict)
                and bool(payload.get("window_id") or payload.get("compaction_response_id"))
            )
            if compacted:
                compaction_seen = True
                result["last_compaction_at"] = _timestamp(row.get("timestamp"))
        if (
            runtime == "codex"
            and not capacity_seen
            and row.get("type") == "event_msg"
            and isinstance(payload, dict)
            and payload.get("type") == "token_count"
        ):
            capacity_seen = True
            info = payload.get("info")
            capacity = info.get("model_context_window") if isinstance(info, dict) else None
            if type(capacity) is int and 0 < capacity <= 2**53 - 1:
                result["context_window_tokens"] = capacity
            usage = info.get("last_token_usage") if isinstance(info, dict) else None
            used = usage.get("input_tokens") if isinstance(usage, dict) else None
            # This is occupancy from the latest call, not cumulative spend.
            # Codex input_tokens already includes cached_input_tokens.
            if type(used) is int and 0 <= used <= 2**53 - 1:
                result["context_tokens"] = used
    return result


def _signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_ino, stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns


def project(row: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    runtime = row.get("runtime")
    if runtime not in {"claude-code", "codex"}:
        return unavailable("unsupported_runtime")
    native_id = identity.get("native_id")
    if identity.get("status") != "recorded" or not native_id:
        return unavailable("identity_unavailable")
    cwd = row.get("worktree_path") or row.get("cwd")
    if not cwd:
        return unavailable("transcript_unavailable")
    harness = runtime_for(runtime, "claude" if runtime == "claude-code" else "codex")
    path = harness.locate_transcript(cwd=Path(cwd), session_id=native_id)
    if path is None:
        return unavailable("transcript_unavailable")
    try:
        return dict(_read(path, runtime, native_id, _signature(path)))
    except OSError:
        return unavailable("transcript_unavailable")
