"""Instance-local checkpoint preferences; no schema or provider credentials."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from typing import Any

from duckterm.core.session_api import APIError
from duckterm.helpers import paths

DEFAULTS: dict[str, Any] = {
    "lookback_records": "all",
    # Sonnet over Haiku, measured 2026-10-31 on two 1,800-2,000-message sessions:
    # 0-1 wrong resume answers per run against 2-4, and ~10 s per span against ~30 s.
    "writer_model": "sonnet",
    # Owner, 2026-11-15: off until the catch-up waves are done.
    "automatic": False,
    # Server-recorded [off_ms, on_ms] periods. Messages written in a closed
    # period are a gap automatic checkpoints never summarize; on_ms is None
    # while still off. Only a switch from on to off opens one.
    "automatic_off": [],
}
SERVER_KEYS = {"automatic_off"}
MAX_OFF_PERIODS = 50
MODELS = {"haiku", "sonnet", "opus"}
# Keys older settings files still carry: batches became byte-bounded spans, and
# spans are not model-reviewed (the deep tier lives in AI models settings).
RETIRED = {"batch_records", "reviewer_model"}


def validated(value: Any, *, stored: bool = False) -> dict[str, Any]:
    if isinstance(value, dict) and stored:
        value = {k: v for k, v in value.items() if k not in RETIRED}
    allowed = set(DEFAULTS) if stored else set(DEFAULTS) - SERVER_KEYS
    if not isinstance(value, dict) or set(value) - allowed:
        raise APIError(400, "Unknown checkpoint setting")
    result = {**DEFAULTS, **value}
    count = result["lookback_records"]
    if count != "all" and (type(count) is not int or not 1 <= count <= 1000):
        raise APIError(400, "Checkpoint lookback must be all or an integer from 1 to 1000 records")
    if not isinstance(result["writer_model"], str) or result["writer_model"] not in MODELS:
        raise APIError(400, "Choose haiku, sonnet or opus for the summary writer")
    if type(result["automatic"]) is not bool:
        raise APIError(400, "Automatic checkpoints must be on or off")
    periods = result["automatic_off"]
    if not isinstance(periods, list) or any(
        not isinstance(p, list)
        or len(p) != 2
        or type(p[0]) is not int
        or (p[1] is not None and (type(p[1]) is not int or p[1] < p[0]))
        for p in periods
    ):
        raise APIError(400, "Invalid automatic checkpoint history")
    return result


def off_periods(settings: dict[str, Any]) -> list[tuple[int, int]]:
    """Closed periods with automatic checkpoints off, as (off_ms, on_ms)."""
    return [(p[0], p[1]) for p in settings["automatic_off"] if p[1] is not None]


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
        now = int(time.time() * 1000)
        periods = [list(p) for p in current["automatic_off"]]
        if current["automatic"] and not updated["automatic"]:
            periods.append([now, None])
        elif updated["automatic"] and periods and periods[-1][1] is None:
            periods[-1][1] = now
        updated["automatic_off"] = periods[-MAX_OFF_PERIODS:]
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
