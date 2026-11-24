"""Bounded, process-local AI timings. Never retain prompts, replies or exceptions.

Marks are elapsed milliseconds, not separate durations. In particular OS process
spawn is not CLI readiness, and first stdout is not necessarily the first token.
No disk writes, subprocesses or callbacks run while recording diagnostics.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

_RECENT: deque[dict[str, Any]] = deque(maxlen=64)
_LOCK = Lock()
_PARENT: ContextVar[str] = ContextVar("ai_call_parent", default="")


@dataclass
class Measurement:
    operation: str
    backend: str = ""
    model: str = ""
    tier: str = ""
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str = field(default_factory=_PARENT.get)
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    started: float = field(default_factory=time.monotonic)
    marks: dict[str, float] = field(default_factory=dict)
    input_bytes: int = 0
    output_bytes: int = 0
    status: str = "running"

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, round((time.monotonic() - self.started) * 1000, 3))

    def record(self) -> None:
        self.mark("total_ms")
        value = {
            "call_id": self.call_id,
            "parent_id": self.parent_id,
            "started_at_ms": self.started_at_ms,
            "operation": self.operation,
            "backend": self.backend if self.backend in {"claude-code", "codex", "api"} else "",
            "model": self.model if self.model in {"haiku", "sonnet", "opus"} else "custom",
            "tier": self.tier if self.tier in {"fast", "deep"} else "",
            "status": self.status,
            "input_bytes": self.input_bytes,
            "output_bytes": self.output_bytes,
            "marks": dict(self.marks),
        }
        with _LOCK:
            _RECENT.append(value)


@contextmanager
def measure(
    operation: str, *, backend: str = "", model: str = "", tier: str = ""
) -> Iterator[Measurement]:
    value = Measurement(operation, backend=backend, model=model, tier=tier)
    token = _PARENT.set(value.call_id)
    try:
        yield value
    except BaseException as exc:
        if value.status == "running":
            value.status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        raise
    else:
        value.status = "ok"
    finally:
        _PARENT.reset(token)
        value.record()


def recent() -> list[dict[str, Any]]:
    with _LOCK:
        return [{**item, "marks": dict(item["marks"])} for item in _RECENT]
