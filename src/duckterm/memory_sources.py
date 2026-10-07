"""Read exact native conversations; retained bytes live with existing checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from duckterm.core.saved_progress import file_version
from duckterm.core.session_api import APIError
from duckterm.harnesses import runtime_for
from duckterm.helpers import paths
from duckterm.persistence.saved_state import fingerprint

MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 8 * 1024 * 1024


def directory(key: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key) or key in {".", ".."}:
        raise APIError(400, "Invalid session identity")
    root = paths.home() / "checkpoints"
    target = root / key / "memory"
    for part in (root, root / key, target):
        if part.is_symlink():
            raise APIError(409, "Memory storage must not be a symbolic link")
        part.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


def identity(key: str, runtime: str, native_id: str) -> str:
    return fingerprint([key, runtime, native_id])[:32]


def native_path(source: dict[str, Any]) -> Path | None:
    runtime, native = source["runtime"], source.get("native_id")
    if runtime not in {"claude-code", "codex"}:
        return None
    if not isinstance(native, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", native):
        return None
    # Explicit checkpoint locators are server-written, never supplied by a read request.
    recorded = source.get("path")
    if recorded and Path(recorded).is_file():
        return Path(recorded)
    return runtime_for(runtime, "").locate_transcript(
        cwd=Path(source.get("cwd") or "."), session_id=native
    )


def flatten(message: dict[str, Any]) -> str:
    pieces = []
    for block in message.get("blocks", []):
        if block.get("type") == "tool_use":
            pieces.append(
                "Tool "
                + str(block.get("name") or "tool")
                + ": "
                + json.dumps(block.get("input"), ensure_ascii=False)
            )
        elif isinstance(block.get("text"), str):
            pieces.append(block["text"])
    return "\n".join(pieces)


def read_native(key: str, source: dict[str, Any], *, retain: bool = False) -> dict[str, Any]:
    """Stream and validate raw bytes before exposing normalized text/tool records."""
    path: Path | None
    snapshot = source.get("snapshot")
    if snapshot is not None:
        if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot):
            raise APIError(409, "Invalid retained conversation reference")
        path = directory(key) / (snapshot + ".jsonl")
    else:
        path = native_path(source)
    if path is None:
        raise APIError(409, "Native conversation is unavailable")
    before = file_version(str(path))
    if before is None:
        raise APIError(409, "Native conversation is unavailable")
    if before[2] > MAX_SOURCE_BYTES:
        raise APIError(413, "Conversation exceeds the current read bound")
    if source["runtime"] == "claude-code":
        from duckterm.runtimes.claude_code import _parse_message_lines as parser
    elif source["runtime"] == "codex":
        from duckterm.runtimes.codex import _parse_message_lines as parser
    else:
        raise APIError(409, "Memory reader is unavailable for this harness")
    temporary = None
    output = None
    if retain and snapshot is None:
        fd, name = tempfile.mkstemp(prefix=".capture-", dir=directory(key))
        temporary = Path(name)
        output = os.fdopen(fd, "wb")
    records = []
    digest = hashlib.sha256()
    size = text_size = attachments = 0
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            import stat

            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise APIError(409, "Conversation must be a regular file")
            index = 0
            while raw := stream.readline(MAX_LINE_BYTES + 1):
                size += len(raw)
                if len(raw) > MAX_LINE_BYTES or size > MAX_SOURCE_BYTES:
                    raise APIError(413, "Conversation exceeds the current read bound")
                digest.update(raw)
                if output is not None:
                    output.write(raw)
                if not raw.strip():
                    index += 1
                    continue
                try:
                    line = raw.decode("utf-8")
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        raise ValueError
                except (ValueError, UnicodeError) as exc:
                    raise APIError(
                        409, "Conversation has an incomplete or unreadable record"
                    ) from exc
                payload = obj.get("message", obj.get("payload", {}))
                content = payload.get("content") if isinstance(payload, dict) else None
                if isinstance(content, list):
                    attachments += sum(
                        isinstance(b, dict)
                        and b.get("type") in {"image", "input_image", "document", "audio"}
                        for b in content
                    )
                try:
                    messages = parser([line], index)
                except (ValueError, TypeError, AttributeError, KeyError) as exc:
                    raise APIError(409, "Conversation has an unreadable message record") from exc
                for message in messages:
                    text = flatten(message)
                    if text:
                        text_size += len(text.encode())
                        if text_size > MAX_TEXT_BYTES:
                            raise APIError(413, "Conversation text exceeds the current read bound")
                        records.append(
                            {
                                "id": str(message["id"]),
                                "role": message["role"],
                                "text": text,
                                "message_key": hashlib.sha256(
                                    json.dumps(
                                        [source["runtime"], source["native_id"], message],
                                        sort_keys=True,
                                        ensure_ascii=True,
                                    ).encode()
                                ).hexdigest(),
                            }
                        )
                index += 1
        if before != file_version(str(path)):
            raise APIError(409, "Conversation changed while reading; retry")
        version = digest.hexdigest()
        if snapshot is not None and version != snapshot:
            raise APIError(409, "Retained conversation checksum does not match")
        if output is not None:
            output.flush()
            os.fsync(output.fileno())
            output.close()
            output = None
            destination = directory(key) / (version + ".jsonl")
            if destination.is_symlink():
                raise APIError(409, "Retained conversation must not be a symbolic link")
            assert temporary is not None
            os.replace(temporary, destination)
            temporary = None
        return {
            **source,
            "version": version,
            "path": str(path),
            "fence": before,
            "records": records,
            "bytes": size,
            "unprocessed_attachments": attachments,
            "coverage": "parsed_text_and_tool_records",
            **({"snapshot": version} if retain else {}),
        }
    except OSError as exc:
        raise APIError(409, "Conversation could not be read or retained") from exc
    finally:
        if output is not None:
            output.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
