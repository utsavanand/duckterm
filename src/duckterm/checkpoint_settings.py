"""Instance-local checkpoint preferences; no schema or provider credentials."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from typing import Any

from duckterm.core.session_api import APIError
from duckterm.helpers import paths

DEFAULTS: dict[str, Any] = {
    "lookback_records": 20,
    "batch_records": 20,
    "writer_model": "haiku",
    "reviewer_model": "sonnet",
}
MODELS = {"haiku", "sonnet", "opus"}


def validated(value: Any, *, stored: bool = False) -> dict[str, Any]:
    allowed = set(DEFAULTS) if stored else set(DEFAULTS) - {"batch_records"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise APIError(400, "Unknown checkpoint setting")
    result = {**DEFAULTS, **value}
    count = result["lookback_records"]
    if type(count) is not int or not 1 <= count <= 1000:
        raise APIError(400, "Checkpoint lookback must be an integer from 1 to 1000 records")
    if type(result["batch_records"]) is not int or result["batch_records"] != 20:
        raise APIError(400, "Checkpoint batches contain at most 20 records")
    if any(
        result[k] not in MODELS
        for k in ("writer_model", "reviewer_model")
        if isinstance(result[k], str)
    ) or any(not isinstance(result[k], str) for k in ("writer_model", "reviewer_model")):
        raise APIError(400, "Choose haiku, sonnet or opus for each checkpoint model")
    return result


def load() -> dict[str, Any]:
    path = paths.home() / "checkpoint-settings.json"
    try:
        return validated(json.loads(path.read_text()), stored=True)
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (ValueError, OSError, APIError) as exc:
        raise APIError(
            409, "Checkpoint settings could not be read; save valid settings again"
        ) from exc


def save(value: Any) -> dict[str, Any]:
    validated(value)
    root = paths.home()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "checkpoint-settings.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            current = load()
        except APIError:
            current = dict(DEFAULTS)
        updated = validated({**current, **value}, stored=True)
        fd, name = tempfile.mkstemp(prefix=".checkpoint-settings-", dir=root)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(updated, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, root / "checkpoint-settings.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return updated
