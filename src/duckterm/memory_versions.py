"""Retain exact referenced text versions; authorization and references remain canonical."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from duckterm.core.session_api import APIError
from duckterm.memory_sources import directory

MAX_BYTES = 70 * 1024 * 1024
FIELDS = ("id", "version", "kind", "title", "artifact_id", "records")


def _identity(value: dict[str, Any]) -> None:
    if not (
        isinstance(value.get("id"), str)
        and re.fullmatch(r"[a-f0-9]{32}", value["id"])
        and isinstance(value.get("version"), str)
        and re.fullmatch(r"[a-f0-9]{64}", value["version"])
        and value.get("kind") not in {None, "conversation", "attachment"}
    ):
        raise APIError(409, "Invalid retained text identity")


def retain(key: str, source: dict[str, Any], root: str) -> dict[str, Any]:
    """Called off-loop before committing a checkpoint/revision/evidence reference."""
    _identity(source)
    value = {k: source[k] for k in FIELDS if k in source}
    value["root"] = root
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    if len(raw) > MAX_BYTES:
        raise APIError(413, "Referenced text exceeds retention limit")
    digest = hashlib.sha256(raw).hexdigest()
    dest = directory(key) / ("record-" + digest + ".json")
    if dest.is_symlink():
        raise APIError(409, "Retained text must not be a symbolic link")
    fd, name = tempfile.mkstemp(prefix=".text-", dir=dest.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)
    return {
        **{k: source[k] for k in FIELDS if k in source and k != "records"},
        "root": root,
        "text_snapshot": digest,
    }


def read(key: str, reference: dict[str, Any], root: str) -> dict[str, Any]:
    """Caller must first prove a live authorized canonical reference to this version."""
    _identity(reference)
    digest = reference.get("text_snapshot")
    if reference.get("root") != root:
        raise APIError(403, "Retained text belongs to another sharing scope")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise APIError(409, "Invalid retained text reference")
    path = directory(key) / ("record-" + digest + ".json")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise APIError(409, "Retained text must be a regular file")
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise APIError(409, "Retained text checksum does not match")
        value = json.loads(raw)
        if not isinstance(value, dict) or any(
            value.get(k) != reference.get(k) for k in ("id", "version", "kind", "root")
        ):
            raise APIError(409, "Retained text identity does not match")
        if not isinstance(value.get("records"), list) or any(
            not isinstance(r, dict)
            or not all(isinstance(r.get(k), str) for k in ("id", "role", "text"))
            for r in value["records"]
        ):
            raise APIError(409, "Retained text records are unreadable")
        return value
    except (OSError, ValueError) as exc:
        raise APIError(409, "Retained text is unavailable") from exc
