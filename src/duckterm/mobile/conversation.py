"""Bounded plain conversation text from an explicitly recorded native identity."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.harnesses import runtime_for

if TYPE_CHECKING:
    from duckterm.server import Server

MAX_MESSAGES = 100
MAX_BYTES = 256 * 1024
MAX_READ_BYTES = 1024 * 1024
SUPPORTED = {"claude-code", "codex"}


class ConversationUnavailable(ValueError):
    """Map to 503 using only reason, never a filesystem exception or path."""

    def __init__(self, reason: str = "source_unavailable") -> None:
        self.reason = reason
        super().__init__(reason)


class ConversationNotFound(ConversationUnavailable):
    """The session was absent/deleted; callers may map this to 404."""

    def __init__(self) -> None:
        super().__init__("session_not_found")


@dataclass(frozen=True)
class TrustedMessageSource:
    runtime: str
    cwd: str
    native_id: str
    # Capture generation/detachment boundaries without rejecting ordinary new turns.
    restart_json: str
    started_at: int


def trusted_message_source(server: Server, session_key: str) -> TrustedMessageSource:
    """Copy provenance on the history owner thread; never discover by directory recency."""
    row = server.history.session(session_key)
    if row is None or row.get("test"):
        raise ConversationNotFound()
    runtime = row.get("runtime")
    try:
        identity = server.history.native_identity(session_key)
        started_at = int(row["started_at"])
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ConversationUnavailable() from None
    native_id = identity.get("native_id")
    cwd = row.get("worktree_path") or row.get("cwd")
    if (
        runtime not in SUPPORTED
        or identity.get("status") != "recorded"
        or not isinstance(native_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,200}", native_id) is None
        or not isinstance(cwd, str)
        or not cwd
    ):
        raise ConversationUnavailable()
    return TrustedMessageSource(
        runtime, cwd, native_id, str(row.get("restart_json") or ""), started_at
    )


def conversation_available(server: Server, session_key: str) -> bool:
    """Cheap eligibility only; the read endpoint verifies file availability off-loop."""
    try:
        trusted_message_source(server, session_key)
    except ConversationUnavailable:
        return False
    return True


def _timestamp(value: object) -> int | None:
    if isinstance(value, str):
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(date.timestamp() * 1000) if date.tzinfo is not None else None
        except (ValueError, OverflowError, OSError):
            return None
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and 0 <= value < 10**15
        and math.isfinite(value)
    ):
        return int(value if value >= 10**11 else value * 1000)
    return None


def _plain(content: object, kinds: set[str]) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        part["text"]
        for part in content
        if isinstance(part, dict)
        and part.get("type") in kinds
        and isinstance(part.get("text"), str)
    )


def _message(obj: dict[str, Any], runtime: str) -> tuple[str, str] | None:
    if runtime == "claude-code":
        if obj.get("type") not in {"user", "assistant"}:
            return None
        if obj.get("isMeta") or obj.get("promptSource") == "system":
            return None
        message = obj.get("message")
        if not isinstance(message, dict) or message.get("role") != obj["type"]:
            return None
        text = _plain(message.get("content"), {"text"})
        role = message["role"]
        if role == "user" and text.lstrip().startswith(("<local-command-", "<command-")):
            return None
    else:
        if obj.get("type") != "response_item":
            return None
        message = obj.get("payload")
        if not isinstance(message, dict) or message.get("type") != "message":
            return None
        role = message.get("role")
        if (
            role not in {"user", "assistant"}
            or message.get("channel") == "analysis"
            or message.get("phase") == "analysis"
        ):
            return None
        text = _plain(message.get("content"), {"text", "input_text", "output_text"})
        if role == "user" and text.lstrip().startswith(
            ("<environment_context>", "<user_instructions>")
        ):
            return None
    # Strip terminal controls; plain text is never a raw terminal transcript.
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32 and ord(char) != 127)
    return (role, text) if text.strip() else None


def _size(value: object) -> int:
    # Match the bridge's conservative default JSON encoding, including escaped Unicode.
    return len(json.dumps(value).encode())


def _fit(message: dict[str, Any]) -> dict[str, Any]:
    """A single oversized reply still has a useful, explicitly truncated preview."""
    low, high = 0, len(message["text"])
    while low < high:
        middle = (low + high + 1) // 2
        if _size([{**message, "text": message["text"][:middle]}]) <= MAX_BYTES:
            low = middle
        else:
            high = middle - 1
    return {**message, "text": message["text"][:low]}


def _read(source: TrustedMessageSource) -> dict[str, Any]:
    adapter = runtime_for(source.runtime, "")
    path = adapter.locate_transcript(cwd=Path(source.cwd), session_id=source.native_id)
    if path is None or path.resolve() != path.absolute():
        raise ConversationUnavailable()
    # Locators return exact native identities. Reject symlinks and non-regular files.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ConversationUnavailable()
        start = max(0, before.st_size - MAX_READ_BYTES)
        os.lseek(fd, start, os.SEEK_SET)
        data = os.read(fd, min(before.st_size, MAX_READ_BYTES))
        after = os.fstat(fd)
        current = path.stat()
        if (
            (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
            or path.resolve() != path.absolute()
            or after.st_size < before.st_size
            or after.st_size == before.st_size
            and after.st_mtime_ns != before.st_mtime_ns
        ):
            raise ConversationUnavailable("source_changed")
    finally:
        os.close(fd)
    truncated = start > 0
    if start:
        # The preceding record may begin before the tail boundary; never parse a fragment.
        boundary = data.find(b"\n")
        start += boundary + 1 if boundary >= 0 else len(data)
        data = data[boundary + 1 :] if boundary >= 0 else b""
    messages: list[dict[str, Any]] = []
    size = 2  # JSON array brackets; separators below match json.dumps defaults.
    for raw in data.splitlines(keepends=True):
        offset = start
        start += len(raw)
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeError):
            truncated = True
            continue
        if not isinstance(obj, dict) or (parsed := _message(obj, source.runtime)) is None:
            continue
        role, text = parsed
        identity = json.dumps([source.runtime, source.native_id, offset]).encode() + raw
        message = {
            "id": hashlib.sha256(identity).hexdigest(),
            "role": role,
            "text": text,
            "at": _timestamp(obj.get("timestamp")),
        }
        if _size([message]) > MAX_BYTES:
            message = _fit(message)
            truncated = True
        size += _size(message) + (2 if messages else 0)
        messages.append(message)
        while len(messages) > MAX_MESSAGES or size > MAX_BYTES:
            size -= _size(messages.pop(0)) + (2 if messages else 0)
            truncated = True
    return {"messages": messages, "truncated": truncated}


async def read_conversation(server: Server, session_key: str) -> dict[str, Any]:
    """Return <=100 plain turns / 256 KiB JSON; DB work stays on its owner thread.

    Unsupported/missing sources raise ConversationUnavailable; missing sessions
    raise ConversationNotFound. No exception contains a path or native transcript.
    Callers must independently recheck device authorization after awaiting this.
    """
    source = trusted_message_source(server, session_key)
    try:
        result = await asyncio.to_thread(_read, source)
    except (OSError, ValueError, TypeError) as exc:
        reason = exc.reason if isinstance(exc, ConversationUnavailable) else "source_unavailable"
        raise ConversationUnavailable(reason) from None
    if trusted_message_source(server, session_key) != source:
        raise ConversationUnavailable("source_changed")
    return result
