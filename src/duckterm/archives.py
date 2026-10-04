"""Durable archive grace periods; Undo never tries to resurrect a killed process."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError

if TYPE_CHECKING:
    from duckterm.server import Server

UNDO_MS = 8000


class Archives:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def pending(self, key: str) -> bool:
        return bool(self.server.history.archive_request(key))

    def request(self, key: str) -> dict[str, Any]:
        from duckterm import transfers

        row = self.server.history.session(key)
        if not row:
            raise APIError(404, "Session not found")
        if not row.get("launched"):
            raise APIError(400, "only Duckterm-launched sessions can be archived")
        if row.get("state") == "archived":
            raise APIError(409, "Session is already archived")
        if (
            self.server.restarts.pending(key)
            or key in self.server._transfer_sources
            or transfers.session_transfer(key)
        ):
            raise APIError(409, "Finish or cancel the restart or transfer before archiving")
        value = self.server.history.archive_request(key)
        if not value:
            value = {
                "id": uuid.uuid4().hex,
                "session_key": key,
                "name": row.get("name") or key,
                "deadline": int(time.time() * 1000) + UNDO_MS,
                "status": "pending",
            }
            # Commit before acknowledging the request or removing the UI row.
            self.server.history.set_archive_request(key, value)
        self.start(key)
        return value

    def cancel(self, key: str, identifier: str) -> None:
        value = self.server.history.archive_request(key)
        if not value or value["id"] != identifier:
            raise APIError(409, "This archive request is no longer pending")
        if value["status"] != "pending" or time.time() * 1000 >= value["deadline"]:
            raise APIError(409, "The archive undo window has expired")
        # No await between checking the deadline and the durable cancellation.
        self.server.history.set_archive_request(key, None)
        task = self.tasks.pop(key, None)
        if task:
            task.cancel()

    def start(self, key: str) -> None:
        if key not in self.tasks or self.tasks[key].done():
            self.tasks[key] = asyncio.create_task(self.run(key))

    def recover(self) -> None:
        for value in self.server.history.archive_requests():
            self.start(value["session_key"])

    async def run(self, key: str) -> None:
        try:
            while value := self.server.history.archive_request(key):
                delay = max(0, (value["deadline"] - time.time() * 1000) / 1000)
                if value["status"] == "pending" and delay:
                    await asyncio.sleep(delay)
                    continue
                # Once claimed, even a late/stale Undo cannot cross the stop boundary.
                value["status"] = "committing"
                self.server.history.set_archive_request(key, value)
                try:
                    await self.server._commit_archive(key)
                except Exception as exc:
                    # Keep the durable operation visible and retry. Shutdown cancellation
                    # is a BaseException, leaving the operation for the next server.
                    value["error"] = str(exc)
                    self.server.history.set_archive_request(key, value)
                    await asyncio.sleep(2)
                    continue
                self.server.history.set_archive_request(key, None)
        finally:
            if self.tasks.get(key) is asyncio.current_task():
                self.tasks.pop(key, None)

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
