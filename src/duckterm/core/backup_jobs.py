"""One manual backup at a time, with private durable configuration and results."""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from duckterm.helpers.private_files import private_read, private_write
from duckterm.persistence import backup, backup_sync


class BackupJobs:
    def __init__(self, state_path: Path) -> None:
        self.path = state_path
        raw = private_read(state_path)
        self.state: dict[str, Any] = json.loads(raw) if raw else {"destination": None, "job": None}
        if not isinstance(self.state, dict) or set(self.state) != {"destination", "job"}:
            raise ValueError("Invalid backup state file")
        self.task: asyncio.Task[None] | None = None
        job = self.state["job"]
        if job and job["status"] == "running":
            job.update(
                status="interrupted",
                finished_at=int(time.time() * 1000),
                error=(
                    "The server restarted before completion was recorded. "
                    "Check the destination before retrying."
                ),
            )
            self._save()

    def _save(self) -> None:
        private_write(self.path, json.dumps(self.state))

    def snapshot(self) -> dict[str, Any]:
        # A request must not mutate an object still owned by a background task.
        return json.loads(json.dumps(self.state))  # type: ignore[no-any-return]

    def configure(self, destination: Any) -> None:
        if not isinstance(destination, str) or not destination.strip():
            raise ValueError("Configure a local path or gs:// bucket prefix before backing up")
        if len(destination.encode()) > 4096:
            raise ValueError("Backup destination exceeds 4096 bytes")
        backup.validate_destination(destination)
        previous = self.state["destination"]
        self.state["destination"] = destination
        try:
            self._save()
        except (OSError, ValueError):
            self.state["destination"] = previous
            raise

    def start(self, mode: str = "archive") -> dict[str, Any]:
        if self.task and not self.task.done():
            raise RuntimeError("A backup is already running")
        destination = self.state["destination"]
        if not destination:
            raise ValueError("Configure a local path or gs:// bucket prefix before backing up")
        backup_sync.validate_mode(destination, mode)
        job = {
            "mode": mode,
            "id": uuid.uuid4().hex,
            "status": "running",
            "destination": destination,
            "started_at": int(time.time() * 1000),
            "finished_at": None,
            "archive_path": None,
            "result": None,
            "error": None,
        }
        previous = self.state["job"]
        self.state["job"] = job
        try:
            self._save()
        except (OSError, ValueError):
            self.state["job"] = previous
            raise
        self.task = asyncio.create_task(self._run(job, destination, mode))
        return self.snapshot()

    async def _run(self, job: dict[str, Any], destination: str, mode: str) -> None:
        try:
            if mode == "sync":
                synced = await asyncio.to_thread(backup_sync.create, destination)
                result, archive_path = synced.result, synced.archive_path
            else:
                result = await asyncio.to_thread(backup.create, destination)
                archive_path = (
                    result.rsplit("\nUploaded to ", 1)[0]
                    if destination.startswith("gs://")
                    else result
                )
            job.update(status="succeeded", result=result, archive_path=archive_path)
        except backup.BackupUploadError as exc:
            job.update(status="failed", error=str(exc), archive_path=exc.archive_path)
        except Exception as exc:  # noqa: BLE001 — background job reports failures to its owner
            job.update(status="failed", error=str(exc))
        job["finished_at"] = int(time.time() * 1000)
        try:
            self._save()
        except (OSError, ValueError) as exc:
            # Preserve the in-memory result; do not rerun or lose a completed archive.
            job["error"] = f"{job['error'] or ''} Could not save backup status: {exc}".strip()
