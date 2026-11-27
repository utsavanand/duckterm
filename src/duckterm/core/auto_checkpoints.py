"""Automatic checkpoints (owner, 2026-11-15).

An agent is due after 50 new messages, or after 15 quiet minutes with at least
one new message, counted since its last checkpoint. The 50-message trigger waits
for a pause (a turn end, or a quiet minute): mid-turn, the conversation file
changes while it is copied and the checkpoint fails. At most two run at once,
the agent with the most new messages first. There is no daily cap. Agents idle
for over 7 days are not caught up until they become active again. A failed
checkpoint is still a checkpoint row, so it is retried only after new messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import sys
import time
from typing import Any

from duckterm import checkpoint_settings, memory_checkpoint
from duckterm.core.session_api import APIError

NEW_MESSAGES = 50
QUIET_MS = 15 * 60 * 1000
SETTLED_MS = 60 * 1000
ACTIVE_MS = 7 * 24 * 60 * 60 * 1000
MAX_RUNNING = 2
TICK_SECONDS = 30
# Owner prompts and turn ends; both harnesses send them.
MESSAGE_EVENTS = ("UserPromptSubmit", "Stop")


def due(conn: Any, now: int, busy: set[str]) -> list[str]:
    """Agents ready for an automatic checkpoint, most new messages first."""
    ready: list[tuple[int, str]] = []
    for row in conn.execute(
        "SELECT s.session_key, s.started_at, "
        "(SELECT max(created_at) FROM checkpoints c WHERE c.session_key=s.session_key) "
        "FROM sessions s WHERE s.test=0 AND s.merged_into IS NULL AND s.updated_at>?",
        (now - ACTIVE_MS,),
    ):
        key, started, last = row[0], row[1], row[2]
        if key in busy:
            continue
        count, latest = conn.execute(
            "SELECT count(*), max(ts) FROM events WHERE session_key=? AND ts>? "
            f"AND event_type IN ({','.join('?' * len(MESSAGE_EVENTS))})",
            (key, last or started or 0, *MESSAGE_EVENTS),
        ).fetchone()
        if count and now - latest >= QUIET_MS:
            ready.append((count, key))
        elif count >= NEW_MESSAGES:
            last_type, last_ts = conn.execute(
                "SELECT event_type, ts FROM events WHERE session_key=? ORDER BY ts DESC LIMIT 1",
                (key,),
            ).fetchone()
            if last_type == "Stop" or now - last_ts >= SETTLED_MS:
                ready.append((count, key))
    return [key for _, key in sorted(ready, key=lambda pair: -pair[0])]


class AutoCheckpoints:
    def __init__(self, server: Any) -> None:
        self.server = server
        self.running: dict[str, asyncio.Task[Any]] = {}

    def tick(self, now: int) -> list[str]:
        """Start due checkpoints up to the concurrency limit; returns the keys started."""
        if not checkpoint_settings.load()["automatic"]:
            return []
        free = MAX_RUNNING - len(self.running)
        if free <= 0:
            return []
        # A pending restart or harness switch makes its own checkpoint.
        started = [
            key
            for key in due(self.server.history._conn, now, set(self.running))
            if not self.server.restarts.pending(key)
        ][:free]
        for key in started:
            task = asyncio.get_running_loop().create_task(self._run(key))
            self.running[key] = task
            task.add_done_callback(functools.partial(self._finished, key))
        return started

    def _finished(self, key: str, _task: asyncio.Task[Any]) -> None:
        self.running.pop(key, None)

    async def _run(self, key: str) -> None:
        row = self.server.history.session(key)
        if row is None:
            return
        try:
            await self.server._create_checkpoint(key, row, memory_checkpoint.AUTOMATIC_LABEL)
        except APIError as exc:
            # Our own fixed messages; they say which step refused.
            print(f"[duckterm] automatic checkpoint failed: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — background boundary; the next trigger retries
            print(f"[duckterm] automatic checkpoint failed: {type(exc).__name__}", file=sys.stderr)

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                self.tick(int(time.time() * 1000))
            except Exception as exc:  # noqa: BLE001 — a bad tick must not stop the loop
                print(f"[duckterm] automatic checkpoints: {type(exc).__name__}", file=sys.stderr)

    async def close(self) -> None:
        for task in list(self.running.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
