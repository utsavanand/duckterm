"""Read exact native conversations; retained records live with existing checkpoints."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import tempfile
from datetime import datetime
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
# Top-level type only: Codex writes {"timestamp":…,["ordinal":…,]"type":"compacted",…}.
# Leading fields must be scalars, so a "type" nested in an object never matches.
COMPACTED = re.compile(rb'\{(?:\s*"\w+"\s*:\s*(?:"[^"\\]*"|-?\d+)\s*,)*\s*"type"\s*:\s*"compacted"')
NORMALIZATION = "native-blocks-v2"
# A retained version keeps the extracted records, not the native bytes: raw files
# carry metadata, hidden reasoning and Codex compaction copies (a 521 MB rollout
# held 1.15 MB of text). The version is still the digest of the native bytes, so
# evidence handles stay the same; the records carry their own digest.
RECORDS_FORMAT = "records-v1"


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


def _record_line(record: dict[str, Any]) -> bytes:
    # Most messages have no tool blocks, so their summary text is the text itself.
    stored = {k: v for k, v in record.items() if not (k == "summary_text" and v == record["text"])}
    return (json.dumps(stored, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _records_file(key: str, snapshot: str) -> tuple[Path, dict[str, Any] | None] | None:
    """A version's records copy and, when it is a prefix of a newer one, its pointer.

    None means the version was retained as native bytes (before records-v1).
    """
    root = directory(key)
    path = root / (snapshot + ".records")
    if path.exists() or path.is_symlink():
        return path, None
    try:
        value = json.loads((root / (snapshot + ".records-prefix")).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise APIError(409, "Retained conversation is unavailable") from exc
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("of"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["of"])
        or not isinstance(value.get("sha256"), str)
        or any(
            type(value.get(field)) is not int or value[field] < 0
            for field in ("records", "bytes", "unprocessed_attachments")
        )
    ):
        raise APIError(409, "Invalid retained conversation reference")
    return root / (value["of"] + ".records"), value


def _read_records(key: str, snapshot: str) -> dict[str, Any] | None:
    found = _records_file(key, snapshot)
    if found is None:
        return None
    path, pointer = found
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            if os.fstat(stream.fileno()).st_size > 4 * MAX_TEXT_BYTES:
                raise APIError(413, "Conversation text exceeds the current read bound")
            header, *lines = stream.readlines()
            meta = json.loads(header)
    except (OSError, ValueError) as exc:
        raise APIError(409, "Retained conversation is unavailable") from exc
    if (
        not isinstance(meta, dict)
        or meta.get("format") != RECORDS_FORMAT
        or meta.get("version") != (snapshot if pointer is None else pointer["of"])
    ):
        raise APIError(409, "Retained conversation checksum does not match")
    count, digest = (
        (meta["records"], meta["sha256"])
        if pointer is None
        else (pointer["records"], pointer["sha256"])
    )
    if (len(lines) != count if pointer is None else len(lines) < count) or hashlib.sha256(
        b"".join(lines[:count])
    ).hexdigest() != digest:
        raise APIError(409, "Retained conversation checksum does not match")
    records = []
    for line in lines[:count]:
        record = json.loads(line)
        record.setdefault("summary_text", record["text"])
        records.append(record)
    sizes = meta if pointer is None else pointer
    return {
        "path": path,
        "records": records,
        "bytes": sizes["bytes"],
        "unprocessed_attachments": sizes["unprocessed_attachments"],
        "normalization": meta["normalization"],
    }


def retained_path(key: str, snapshot: str) -> Path:
    """The file a retained version is read from, for cache fences."""
    found = _records_file(key, snapshot)
    return found[0] if found is not None else snapshot_file(key, snapshot)[0]


def _retain_records(
    key: str,
    source: dict[str, Any],
    version: str,
    size: int,
    attachments: int,
    records: list[dict[str, Any]],
    prefixes: dict[int, str],
) -> None:
    """Write one version's records; keep one whole copy per growing conversation."""
    import fcntl

    root = directory(key)
    lines = [_record_line(record) for record in records]
    meta = {
        "format": RECORDS_FORMAT,
        "version": version,
        "bytes": size,
        "unprocessed_attachments": attachments,
        "normalization": NORMALIZATION,
        "records": len(lines),
        "sha256": hashlib.sha256(b"".join(lines)).hexdigest(),
    }
    destination = root / (version + ".records")
    if destination.is_symlink():
        raise APIError(409, "Retained conversation must not be a symbolic link")
    fd, name = tempfile.mkstemp(prefix=".capture-", dir=root)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write((json.dumps(meta) + "\n").encode())
            output.writelines(lines)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
    identity = source["runtime"] + ":" + source["native_id"]
    with (root / "latest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index_path = root / "latest.json"
        try:
            index = json.loads(index_path.read_text())
        except (OSError, ValueError):
            index = {}
        previous = index.get(identity)
        # Raw-byte versions from before records-v1 are left as they are.
        if (
            isinstance(previous, dict)
            and previous.get("format") == RECORDS_FORMAT
            and previous.get("digest") != version
            and prefixes.get(previous.get("bytes", -1)) == previous.get("digest")
        ):
            old = root / (previous["digest"] + ".records")
            count = previous["records"]
            digest = hashlib.sha256(b"".join(lines[:count])).hexdigest()
            try:
                with old.open("rb") as stream:
                    same = json.loads(stream.readline()).get("sha256") == digest
            except (OSError, ValueError):
                same = False
            if same:
                pointer = {
                    "of": version,
                    "records": count,
                    "sha256": digest,
                    "bytes": previous["bytes"],
                    "unprocessed_attachments": previous["unprocessed_attachments"],
                }
                private_write(old.with_suffix(".records-prefix"), json.dumps(pointer))
                # Re-aim older pointers at the whole copy, so every version is one
                # hop away; their counts and digests hold because it extends theirs.
                for other in root.glob("*.records-prefix"):
                    try:
                        value = json.loads(other.read_text())
                    except (OSError, ValueError):
                        continue
                    if isinstance(value, dict) and value.get("of") == previous["digest"]:
                        private_write(other, json.dumps({**value, "of": version}))
                # The pointers exist before the copy goes, so a reader always finds one.
                old.unlink(missing_ok=True)
        index[identity] = {
            "format": RECORDS_FORMAT,
            "digest": version,
            "bytes": size,
            "records": len(lines),
            "unprocessed_attachments": attachments,
        }
        fd, name = tempfile.mkstemp(prefix=".latest-", dir=root)
        with os.fdopen(fd, "w") as stream:
            json.dump(index, stream)
        os.replace(name, index_path)


def _timestamp(value: Any) -> int | None:
    """A native line's ISO-8601 timestamp in epoch ms; None when absent or unreadable."""
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def read_native(key: str, source: dict[str, Any], *, retain: bool = False) -> dict[str, Any]:
    """Stream and validate raw bytes before exposing normalized text/tool records."""
    path: Path | None
    snapshot = source.get("snapshot")
    if snapshot is not None:
        if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot):
            raise APIError(409, "Invalid retained conversation reference")
        stored = _read_records(key, snapshot)
        if stored is not None:
            return {
                **source,
                "version": snapshot,
                "path": str(stored["path"]),
                "fence": None,
                "records": stored["records"],
                "bytes": stored["bytes"],
                "unprocessed_attachments": stored["unprocessed_attachments"],
                "coverage": "parsed_text_and_tool_records",
                "normalization": stored["normalization"],
            }
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
    retaining = retain and snapshot is None
    records = []
    digest = hashlib.sha256()
    size = text_size = attachments = 0
    end = before[2] if limit is None else min(limit, before[2])
    # Digests at line boundaries let a retained copy prove an older one is its prefix.
    previous_sizes: set[int] = set()
    if retaining:
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
                    if source["runtime"] != "codex" or not COMPACTED.match(raw[:512]):
                        raise APIError(413, "Conversation record exceeds the current read bound")
                    # Codex's compaction copy of the conversation is never parsed;
                    # stream it through the hash in bounded chunks. Lines of
                    # 16-23 MB made a 1.5 GB ui-dev history unreadable (2026-11-17).
                    while True:
                        digest.update(raw)
                        if raw.endswith(b"\n") or size >= end:
                            break
                        raw = stream.readline(min(MAX_LINE_BYTES + 1, end - size))
                        if not raw:
                            break
                        size += len(raw)
                    if size in previous_sizes:
                        prefixes[size] = digest.hexdigest()
                    index += 1
                    continue
                digest.update(raw)
                if size in previous_sizes:
                    prefixes[size] = digest.hexdigest()
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
                                # When the harness wrote it, in ms; automatic
                                # checkpoints skip messages written while off.
                                "at": _timestamp(obj.get("timestamp")),
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
        if retaining:
            _retain_records(key, source, version, size, attachments, records, prefixes)
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
