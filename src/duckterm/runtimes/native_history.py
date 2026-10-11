"""Explicit API-backed Messages snapshots; never a native transcript locator."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from duckterm.helpers.private_files import private_write
from duckterm.runtimes.opencode_protocol import OpenCodeError

MAX_HISTORY = 20 * 1024 * 1024


@dataclass(frozen=True)
class NativeSnapshot:
    path: str
    generation: str
    destination: str
    store: str
    session_id: str

    def scope(self) -> list[str]:
        return ["opencode", self.generation, self.destination, self.store, self.session_id]


MessageSource = tuple[str, str, str | None] | NativeSnapshot | None


def root(directory: Path, key: str) -> Path:
    return directory / "opencode" / hashlib.sha256(key.encode()).hexdigest()


def snapshot_path(directory: Path, key: str, generation: str) -> Path:
    # Hash the generation as well: neither native data nor a row key chooses a
    # filesystem component, and stale workers can only write their old file.
    return root(directory, key) / (
        "messages-" + hashlib.sha256(generation.encode()).hexdigest() + ".json"
    )


def encode(
    source: NativeSnapshot, messages: list[dict[str, Any]], observed_at: int, revision: int
) -> str:
    rows = []
    for message in messages:
        message = dict(message)
        raw = json.dumps([source.scope(), message], sort_keys=True, ensure_ascii=True).encode()
        message["message_key"] = hashlib.sha256(raw).hexdigest()
        rows.append(message)
    result = json.dumps(
        {
            "scope": source.scope(),
            "messages": rows,
            "observed_at": observed_at,
            "revision": revision,
        }
    )
    if len(result.encode()) > MAX_HISTORY:
        raise OpenCodeError("Native history exceeds the snapshot bound")
    return result


def save(source: NativeSnapshot, text: str) -> bool:
    # A canceled worker may still finish an atomic write. Serialize writers and
    # refuse older observations, including a prior server's delayed worker.
    candidate = json.loads(text)
    if candidate.get("scope") != source.scope() or type(candidate.get("revision")) is not int:
        raise OpenCodeError("Invalid native history write scope")
    target = Path(source.path)
    lock = os.open(
        str(target) + ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        lock_info = os.fstat(lock)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid():
            raise OpenCodeError("Invalid native history lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read(source)
        watermark = target.with_suffix(".revision")
        try:
            fd = os.open(watermark, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            high = -1
        else:
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_size > 40
                ):
                    raise OpenCodeError("Invalid native history revision")
                try:
                    high = int(stream.read(40))
                except ValueError as exc:
                    raise OpenCodeError("Invalid native history revision") from exc
        if max(previous.get("revision", -1), high) >= candidate["revision"]:
            return False
        # A polling timestamp is not a new transcript revision. Keeping the
        # content file stable lets checkpoint readers fence actual changes.
        if previous.get("messages") != candidate["messages"] or "revision" not in previous:
            private_write(target, text)
        private_write(watermark, str(candidate["revision"]))
        return True
    finally:
        os.close(lock)


def persist(
    source: NativeSnapshot, messages: list[dict[str, Any]], stamp: int, revision: int
) -> bool:
    return save(source, encode(source, messages, stamp, revision))


def read(source: NativeSnapshot) -> dict[str, Any]:
    try:
        fd = os.open(source.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_size > MAX_HISTORY
            ):
                raise OpenCodeError("Invalid native history snapshot")
            raw = stream.read(MAX_HISTORY + 1)
    except FileNotFoundError:
        return {"messages": [], "history_status": "unavailable"}
    if len(raw) > MAX_HISTORY:
        raise OpenCodeError("Native history exceeds the snapshot bound")
    try:
        result = json.loads(raw)
    except ValueError as exc:
        raise OpenCodeError("Invalid native history snapshot") from exc
    if (
        not isinstance(result, dict)
        or result.get("scope") != source.scope()
        or not isinstance(result.get("messages"), list)
        or type(result.get("observed_at")) is not int
        or type(result.get("revision")) is not int
    ):
        raise OpenCodeError("Native history belongs to another conversation or launch")
    # A saved API snapshot is explicitly cached; it never promises current
    # native liveness or a complete live token total when the endpoint is gone.
    return {
        "messages": result["messages"],
        "history_status": "cached",
        "observed_at": result["observed_at"],
        "revision": result["revision"],
    }
