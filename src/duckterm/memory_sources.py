"""Read exact native conversations; retained bytes live with existing checkpoints."""

from __future__ import annotations

import hashlib
import itertools
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
from duckterm.helpers.private_files import private_write
from duckterm.persistence.saved_state import fingerprint

MAX_TEXT_BYTES = 64 * 1024 * 1024
# Codex compaction metadata can exceed 8 MiB even with little parsed text.
# Bound each JSON allocation independently of the accumulated text budget.
MAX_LINE_BYTES = 16 * 1024 * 1024
NORMALIZATION = "native-blocks-v2"


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
    if runtime == "codex":
        matches = list((Path.home() / ".codex" / "sessions").glob(f"**/rollout-*-{native}.jsonl"))
        if len(matches) > 1:
            raise APIError(409, "Multiple native files match this conversation identity")
        return matches[0] if matches else None
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


def normalized(message: dict[str, Any]) -> list[dict[str, Any]]:
    """A native transport role can wrap both typed text and tool output."""
    groups = [
        {"role": role, "blocks": list(blocks)}
        for role, blocks in itertools.groupby(
            message.get("blocks", []),
            key=lambda block: "tool" if block.get("type") == "tool_result" else message["role"],
        )
    ]
    return [
        {**group, "id": message["id"] if len(groups) == 1 else f"{message['id']}:{index}"}
        for index, group in enumerate(groups)
    ]


def snapshot_file(key: str, snapshot: str) -> tuple[Path, int | None]:
    """A retained version is a whole file, or the first N bytes of a newer one.

    Transcripts only grow, so each checkpoint used to keep another whole copy
    (a 107 MB Codex rollout per checkpoint). An older copy whose bytes start the
    newer one is replaced by a pointer; read_native still verifies its digest.
    """
    path = directory(key) / (snapshot + ".jsonl")
    limit: int | None = None
    visited: set[Path] = set()
    while path not in visited:
        visited.add(path)
        if path.exists() or path.is_symlink():
            return path, limit
        pointer = path.with_suffix(".prefix")
        try:
            value = json.loads(pointer.read_text())
            target, length = value["of"], value["bytes"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise APIError(409, "Retained conversation is unavailable") from exc
        if not isinstance(target, str) or not re.fullmatch(r"[a-f0-9]{64}", target):
            raise APIError(409, "Invalid retained conversation reference")
        if type(length) is not int or length < 0:
            raise APIError(409, "Invalid retained conversation reference")
        limit = length if limit is None else min(limit, length)
        path = directory(key) / (target + ".jsonl")
    raise APIError(409, "Retained conversation reference cycle")


def _replace_with_pointer(
    key: str, source: dict[str, Any], version: str, prefixes: dict[int, str]
) -> None:
    """Keep one whole copy per conversation: point the previous copy at this one."""
    import fcntl

    root = directory(key)
    identity = source["runtime"] + ":" + source["native_id"]
    with (root / "latest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index_path = root / "latest.json"
        try:
            index = json.loads(index_path.read_text())
        except (OSError, ValueError):
            index = {}
        previous = index.get(identity)
        if (
            isinstance(previous, dict)
            and previous.get("digest") != version
            and prefixes.get(previous.get("bytes", -1)) == previous.get("digest")
        ):
            old = root / (previous["digest"] + ".jsonl")
            pointer = old.with_suffix(".prefix")
            private_write(pointer, json.dumps({"of": version, "bytes": previous["bytes"]}))
            # Re-aim older pointers at the whole copy, so every version is one
            # hop away; otherwise a year of checkpoints is a chain of thousands.
            # Each rewrite is atomic and either target still reads correctly.
            for other in root.glob("*.prefix"):
                try:
                    value = json.loads(other.read_text())
                except (OSError, ValueError):
                    continue
                if isinstance(value, dict) and value.get("of") == previous["digest"]:
                    private_write(other, json.dumps({**value, "of": version}))
            # The pointers exist before the copy goes, so a reader always finds one.
            old.unlink(missing_ok=True)
        index[identity] = {"digest": version, "bytes": max(prefixes) if prefixes else 0}
        fd, name = tempfile.mkstemp(prefix=".latest-", dir=root)
        with os.fdopen(fd, "w") as stream:
            json.dump(index, stream)
        os.replace(name, index_path)


def read_native(key: str, source: dict[str, Any], *, retain: bool = False) -> dict[str, Any]:
    """Stream and validate raw bytes before exposing normalized text/tool records."""
    path: Path | None
    snapshot = source.get("snapshot")
    if snapshot is not None:
        if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot):
            raise APIError(409, "Invalid retained conversation reference")
        path, limit = snapshot_file(key, snapshot)
    else:
        path, limit = native_path(source), None
    if path is None:
        raise APIError(409, "Native conversation is unavailable")
    before = file_version(str(path))
    if before is None:
        raise APIError(409, "Native conversation is unavailable")
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
    end = before[2] if limit is None else min(limit, before[2])
    # Digests at line boundaries let a retained copy prove an older one is its prefix.
    previous_sizes: set[int] = set()
    if output is not None:
        try:
            latest = json.loads((directory(key) / "latest.json").read_text())
            entry = latest.get(source["runtime"] + ":" + source["native_id"], {})
            if type(entry.get("bytes")) is int:
                previous_sizes.add(entry["bytes"])
        except (OSError, ValueError, AttributeError):
            pass
    prefixes: dict[int, str] = {}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            import stat

            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise APIError(409, "Conversation must be a regular file")
            index = 0
            # Raw history can be large while its parsed conversation is small:
            # Codex stores repeated compaction metadata in the same JSONL file.
            # Bound allocations by line and accumulated text, not total file size.
            # Read only the captured extent so an appending writer cannot keep
            # this scan alive indefinitely; the fence below still rejects changes.
            while size < end and (raw := stream.readline(min(MAX_LINE_BYTES + 1, end - size))):
                size += len(raw)
                if len(raw) > MAX_LINE_BYTES:
                    raise APIError(413, "Conversation record exceeds the current read bound")
                digest.update(raw)
                if size in previous_sizes:
                    prefixes[size] = digest.hexdigest()
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
                if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                    observed = obj["payload"].get("id")
                    if observed and observed != source["native_id"]:
                        raise APIError(409, "Native file contains another conversation identity")
                payload = obj.get("message", obj.get("payload", {}))
                content = payload.get("content") if isinstance(payload, dict) else None
                if "type" in obj and not isinstance(obj["type"], str):
                    raise APIError(409, "Conversation has an unreadable record type")
                if isinstance(content, list):
                    if any(
                        not isinstance(b, dict) or not isinstance(b.get("type"), str)
                        for b in content
                    ):
                        raise APIError(409, "Conversation has an unreadable content block")
                    if any(
                        b["type"] in {"text", "input_text", "output_text"}
                        and not isinstance(b.get("text"), str)
                        for b in content
                    ):
                        raise APIError(409, "Conversation has an unreadable text block")
                    attachments += sum(
                        isinstance(b, dict)
                        and b.get("type") in {"image", "input_image", "document", "audio"}
                        for b in content
                    )
                try:
                    # Codex metadata (especially replacement_history) was decoded
                    # a second time by the message parser only to be discarded.
                    # Integrity/type validation above still covers every raw line.
                    parsed: list[dict[str, Any]] = (
                        []
                        if source["runtime"] == "codex" and obj.get("type") != "response_item"
                        else parser([line], index)
                    )
                    # Preserve raw locators, roles and text. Failure metadata is
                    # used only by the summary projection, never search/read.
                    failures = iter(
                        b.get("is_error") is True
                        for b in (content if isinstance(content, list) else [])
                        if b.get("type") == "tool_result"
                    )
                    failed_blocks = {
                        id(block)
                        for message in parsed
                        for block in message.get("blocks", [])
                        if block.get("type") == "tool_result" and next(failures, False)
                    }
                    if isinstance(payload, dict) and payload.get("is_error") is True:
                        failed_blocks.update(
                            id(block)
                            for message in parsed
                            for block in message.get("blocks", [])
                            if block.get("type") == "tool_result"
                        )
                    messages = [part for message in parsed for part in normalized(message)]
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
                                "role": (
                                    "derived_context"
                                    if message["role"] == "user"
                                    and text.startswith(
                                        "DuckTerm continuation context (derived summary"
                                    )
                                    else message["role"]
                                ),
                                "text": text,
                                "summary_text": flatten(
                                    {
                                        "blocks": [
                                            block
                                            for block in message.get("blocks", [])
                                            if block.get("type") not in {"tool_use", "tool_result"}
                                            or id(block) in failed_blocks
                                        ]
                                    }
                                ),
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
            prefixes[size] = version
            _replace_with_pointer(key, source, version, prefixes)
        return {
            **source,
            "version": version,
            "path": str(path),
            # A retained copy is verified by digest and never changes; the file it
            # resolves through can become a pointer when a newer copy is kept.
            "fence": before if snapshot is None else None,
            "records": records,
            "bytes": size,
            "unprocessed_attachments": attachments,
            "coverage": "parsed_text_and_tool_records",
            "normalization": NORMALIZATION,
            **({"snapshot": version} if retain else {}),
        }
    except OSError as exc:
        raise APIError(409, "Conversation could not be read or retained") from exc
    finally:
        if output is not None:
            output.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
