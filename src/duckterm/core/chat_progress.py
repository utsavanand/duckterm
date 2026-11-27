"""Bounded, owner-only chat activity receipts. No prompts, tokens or model reasoning.

One context per request, shared with its child tasks. Reading progress never
starts or retries an answer. Finished receipts expire without a background job.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

STAGES = frozenset(
    {
        "starting",
        "routing",
        "agents",
        "memories",
        "answer",
        "installation",
        "guide",
        "diff",
        "files",
        "sending",
        "rule",
    }
)
_current: contextvars.ContextVar[Receipt | None] = contextvars.ContextVar(
    "chat_progress", default=None
)


class InvalidProgress(ValueError):
    """Invalid or reused receipt identifier, before the request starts."""


class Receipt:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.finished: float | None = None
        self.state = "running"
        self.stages: list[dict[str, Any]] = []
        self.lock = threading.RLock()
        self.mark("starting")

    def mark(self, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError("Unknown chat activity")
        with self.lock:
            if self.finished is not None or (self.stages and self.stages[-1]["stage"] == stage):
                return
            if len(self.stages) < 64:
                self.stages.append({"stage": stage, "started_ms": self.elapsed()})

    def elapsed(self) -> int:
        return max(0, int(((self.finished or time.monotonic()) - self.started) * 1000))

    def finish(self, state: str) -> None:
        with self.lock:
            if self.finished is None:
                self.finished = time.monotonic()
                self.state = state

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "state": self.state,
                "elapsed_ms": self.elapsed(),
                "stages": [dict(s) for s in self.stages],
            }


class Store:
    def __init__(self, capacity: int = 128, ttl: float = 600) -> None:
        self.rows: dict[str, Receipt] = {}
        self.capacity, self.ttl = capacity, ttl

    def sweep(self) -> None:
        now = time.monotonic()
        self.rows = {
            key: row
            for key, row in self.rows.items()
            if row.finished is None or now - row.finished < self.ttl
        }

    @contextlib.contextmanager
    def track(self, key: str) -> Iterator[None]:
        self.sweep()
        if not re.fullmatch(r"[a-f0-9]{32}", key):
            raise InvalidProgress("Invalid chat progress ID")
        if key in self.rows:
            raise InvalidProgress("Chat progress ID already used; do not resubmit the question")
        # Progress capacity never prevents the underlying answer. Its final
        # response still carries the receipt, but live polling may return 404.
        row = Receipt()
        if len(self.rows) < self.capacity:
            self.rows[key] = row
        token = _current.set(row)
        try:
            yield
        except BaseException:
            row.finish("failed")
            raise
        finally:
            row.finish("finished")
            _current.reset(token)

    def read(self, key: str) -> dict[str, Any] | None:
        self.sweep()
        row = self.rows.get(key)
        return row.snapshot() if row else None


def mark(stage: str) -> None:
    if row := _current.get():
        row.mark(stage)


def attach(entry: dict[str, Any]) -> None:
    """Persist timing beside the answer using the existing JSON conversation."""
    if row := _current.get():
        entry["progress"] = {**row.snapshot(), "state": "finished"}


def response(status: int, payload: Any) -> Any:
    if (row := _current.get()) is not None and isinstance(payload, dict):
        row.finish("finished" if status < 400 else "failed")
        return {**payload, "chat_progress": row.snapshot()}
    return payload


def supported(path: str) -> bool:
    return path in {"/fleet/ask", "/support/ask", "/relay/rules/propose"} or bool(
        re.fullmatch(r"/review/[^/]+/[^/]+/ask|/folders/.+/dispatch|/sessions/[^/]+/message", path)
    )
