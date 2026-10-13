"""Durable request receipts for checkpoint recovery, separate from saved summaries."""

from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
import uuid
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import resolve_checkpoint

if TYPE_CHECKING:
    from duckterm.server import Server

MAX_RECEIPTS = 100


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        raise APIError(400, "Invalid checkpoint request_id")
    return value


class CheckpointRequests:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.tasks: dict[tuple[str, str], asyncio.Task[None]] = {}

    def _receipts(self, key: str) -> dict[str, Any]:
        return dict(self.server.history.restart_control(key).get("checkpoint_receipts", {}))

    def _save(self, key: str, receipt: dict[str, Any]) -> None:
        # Read immediately before writing; never overwrite concurrent restart state.
        control = self.server.history.restart_control(key)
        receipts = dict(control.get("checkpoint_receipts", {}))
        receipts[receipt["request_id"]] = receipt
        while len(receipts) > MAX_RECEIPTS:
            oldest = next(k for k, v in receipts.items() if v["state"] != "running")
            del receipts[oldest]
        self.server.history.set_restart_control(key, {**control, "checkpoint_receipts": receipts})

    def _checkpoint(self, key: str, identity: str) -> dict[str, Any] | None:
        row = self.server.history._conn.execute(
            "SELECT id,label,summary,record_json,markdown_path,created_at FROM checkpoints "
            "WHERE session_key=? AND id=?",
            (key, identity),
        ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["record"] = json.loads(value.pop("record_json"))
        return resolve_checkpoint(self.server.history._conn, key, value)

    def receipt(self, key: str, request_id: str) -> dict[str, Any]:
        identifier(request_id)
        if self.server.history.session(key) is None:
            raise APIError(404, "Session not found")
        stored = self._receipts(key).get(request_id)
        if stored is None:
            raise APIError(404, "Checkpoint request not found or receipt expired")
        receipt: dict[str, Any] = dict(stored)
        result = self._checkpoint(key, receipt["checkpoint_id"])
        if result is not None:
            # Also recovers a crash after the checkpoint commit but before receipt finalization.
            if receipt["state"] != "complete":
                receipt["state"] = "complete"
                receipt.pop("error", None)
                self._save(key, receipt)
            if receipt.get("export_reason"):
                result["export_reason"] = receipt["export_reason"]
            return {**receipt, "result": {**result, "request_id": request_id}}
        if receipt["state"] == "running" and (key, request_id) not in self.tasks:
            receipt.update(state="interrupted", error="checkpoint_interrupted")
            self._save(key, receipt)
        return receipt

    async def request(self, key: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request_id = identifier(payload.get("request_id", uuid.uuid4().hex))
        label = payload.get("label", "checkpoint")
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise APIError(400, "Invalid checkpoint label")
        selection = payload.get("selection")
        if selection is not None:
            from duckterm import checkpoint_plan

            if not isinstance(selection, dict) or set(selection) != {"plan", "records"}:
                raise APIError(400, "Invalid checkpoint selection")
            checkpoint_plan.choice(selection["records"])
            checkpoint_plan.validate_plan(selection["plan"])
        row = self.server.history.session(key)
        if row is None:
            raise APIError(404, "Session not found")
        previous = self._receipts(key).get(request_id)
        if previous:
            if previous["label"] != label:
                raise APIError(409, "Checkpoint request_id already used with a different label")
            if previous.get("selection") != selection:
                raise APIError(
                    409, "Checkpoint request_id already used with different checkpoint options"
                )
        else:
            if any(session == key for session, _ in self.tasks):
                raise APIError(409, "A checkpoint request is already running for this session")
            for old_id, old in self._receipts(key).items():
                if old["state"] == "running":
                    self.receipt(key, old_id)
            receipt: dict[str, Any] = {
                "request_id": request_id,
                "checkpoint_id": uuid.uuid4().hex,
                "label": label,
                "state": "running",
                "created_at": int(time.time() * 1000),
                **({"selection": selection} if selection is not None else {}),
            }
            self._save(key, receipt)
            started = asyncio.create_task(self._run(key, row, receipt))
            self.tasks[key, request_id] = started
            started.add_done_callback(lambda _: self.tasks.pop((key, request_id), None))
        task = self.tasks.get((key, request_id))
        if task is not None:
            # Browser disconnect/cancel cannot cancel an accepted checkpoint operation.
            await asyncio.shield(task)
        receipt = self.receipt(key, request_id)
        if receipt["state"] == "complete":
            return 200, receipt["result"]
        conflict = (
            receipt["state"] == "interrupted" or receipt.get("error") == "checkpoint_source_changed"
        )
        return 409 if conflict else 500, receipt

    async def _run(self, key: str, row: dict[str, Any], receipt: dict[str, Any]) -> None:
        def report(value: dict[str, Any]) -> None:
            receipt["progress"] = {**receipt.get("progress", {}), **value}
            self._save(key, receipt)

        try:
            report({"stage": "checking"})
            result = await self.server._create_checkpoint(
                key,
                row,
                receipt["label"],
                identity=receipt["checkpoint_id"],
                request_id=receipt["request_id"],
                on_progress=report,
                **({"selection": receipt["selection"]} if "selection" in receipt else {}),
            )
            receipt = {**receipt, "state": "complete"}
            if result.get("export_reason"):
                receipt["export_reason"] = "markdown_unavailable"
        except asyncio.CancelledError:
            self._save(key, {**receipt, "state": "interrupted", "error": "checkpoint_interrupted"})
            raise
        except APIError as exc:
            code = "checkpoint_source_changed" if exc.status == 409 else "checkpoint_failed"
            receipt = {**receipt, "state": "failed", "error": code}
        except Exception:
            # No arbitrary exception/provider text in durable receipts.
            traceback.print_exc()
            receipt = {**receipt, "state": "failed", "error": "checkpoint_failed"}
        self._save(key, receipt)

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
