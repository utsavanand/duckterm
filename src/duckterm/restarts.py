"""Durable, exact-conversation restarts. Never infer a turn end from output."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.core import events
from duckterm.core.session_api import APIError
from duckterm.harnesses import runtime_for

if TYPE_CHECKING:
    from duckterm.server import Server

ACTIVE = {"queued", "restarting"}
ACTIVITY = {
    events.SESSION_START,
    events.USER_PROMPT_SUBMIT,
    events.PRE_TOOL_USE,
    events.POST_TOOL_USE,
    events.PERMISSION_REQUEST,
    events.SESSION_END,
}
BINARIES = {"claude-code": "claude", "codex": "codex"}


async def cli_version(binary: str) -> str:
    """Bounded version probe of the binary being launched, never a saved version."""
    proc = await asyncio.create_subprocess_exec(
        binary, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        assert proc.stdout is not None
        async with asyncio.timeout(3):
            output = await proc.stdout.read(4097)
            if len(output) > 4096:
                raise ValueError("CLI version output was too large")
            await proc.wait()
        if proc.returncode:
            raise ValueError("CLI version check failed")
        return output.decode(errors="replace").strip() or "Not reported"
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


class Restarts:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.epochs: dict[str, int] = {}
        self.closing = False
        # Never repeat a destructive step after a crash. A queued request stays
        # visible and waits for the next real Stop hook; a started one needs review.
        for row in server.history.sessions():
            key = row["session_key"]
            data = self.read(key)
            if data.get("status") == "restarting":
                self.save(
                    key,
                    status="failed",
                    error=(
                        "DuckTerm restarted during the restart. Check the terminal before "
                        "retrying."
                    ),
                )

    def read(self, key: str) -> dict[str, Any]:
        return self.server.history.restart_control(key)

    def save(self, key: str, **fields: Any) -> dict[str, Any]:
        value = {**self.read(key), **fields}
        self.server.history.set_restart_control(key, value)
        return value

    def pending(self, key: str) -> bool:
        return self.read(key).get("status") in ACTIVE

    def running(self, key: str) -> bool:
        return self.read(key).get("status") == "restarting"

    def plan(self, key: str) -> tuple[dict[str, Any], Any, Any, str]:
        if self.server.archives.pending(key):
            raise APIError(409, "Archive pending; undo it first")
        from duckterm import transfers

        row = self.server.history.session(key)
        if row is None:
            raise APIError(404, "Session not found")
        sup = self.server.orchestrator.get(key)
        if not sup or not row.get("launched"):
            raise APIError(
                409, "Restart requires a live DuckTerm terminal. Use Resume for a stopped session."
            )
        if row.get("state") in {"stopped", "archived", "terminated", "interrupted"}:
            raise APIError(409, "This session is not running. Use Resume instead.")
        if key in self.server._transfer_sources or transfers.session_transfer(key):
            raise APIError(
                409, "This session has a remote transfer. Complete or cancel that transfer first."
            )
        name = str(row.get("runtime") or "")
        binary = BINARIES.get(name)
        if not binary:
            raise APIError(409, "This harness cannot resume an exact conversation for Restart.")
        rt = runtime_for(name, binary)
        cwd = Path(str(row.get("worktree_path") or row.get("cwd") or "."))
        sid = self.server.history.session_id_for(key)
        if not cwd.is_dir():
            raise APIError(409, "The session's project folder no longer exists.")
        if not rt.can_resume_unambiguously(cwd=cwd, recorded=sid):
            raise APIError(
                409,
                (
                    "Cannot verify which conversation belongs to this session. Resume "
                    "the intended conversation explicitly in the harness first."
                ),
            )
        if not shutil.which(binary):
            raise APIError(409, f"{binary} is not installed on this computer.")
        return row, sup, rt, binary

    async def live_plan(self, key: str) -> tuple[dict[str, Any], Any, Any, str]:
        plan = self.plan(key)
        # tmux may wait for the control stream that this event loop drains.
        # Never wait for its subprocess here, including through a property.
        if not await asyncio.to_thread(getattr, plan[1], "running"):
            raise APIError(409, "This terminal is no longer running. Use Resume instead.")
        if self.plan(key)[1] is not plan[1]:
            raise APIError(409, "The terminal changed. Reopen Restart and try again.")
        return plan

    async def draft_free(self, sup: Any, rt: Any) -> bool:
        stamp = sup.last_owner_input_ms
        if time.time() * 1000 - stamp < 500:
            return False
        screen = await asyncio.to_thread(sup.visible_screen)
        return bool(stamp == sup.last_owner_input_ms and rt.prompt_is_empty(screen))

    def turn_finished(self, key: str) -> bool:
        last = self.server.history.latest_turn_event(key)
        if (
            not last
            or last.get("event_type") != events.STOP
            or not last.get("hook_event")
            or last.get("stop_hook_active") is True
            or last.get("agent_id")
        ):
            return False
        sid = self.server.history.session_id_for(key)
        requested = self.read(key)
        if requested.get("status") == "queued" and requested.get("conversation_id") != sid:
            return False
        return bool(sid and last.get("session_id") == sid)

    async def describe(self, key: str) -> dict[str, Any]:
        data = self.read(key)
        row = self.server.history.session(key)
        if row is None:
            raise APIError(404, "Session not found")
        result = {
            **data,
            "can_restart": False,
            "model": data.get("configured_model") or row.get("model") or "",
        }
        try:
            _, sup, rt, _ = await self.live_plan(key)
            result["can_restart"] = True
            result["draft_clear"] = await self.draft_free(sup, rt)
            result["after_turn"] = not self.turn_finished(key)
            if not result["draft_clear"]:
                result["reason"] = (
                    "There may be unsent text in this terminal. Send it or clear it "
                    "yourself, then retry."
                )
        except APIError as exc:
            result["reason"] = str(exc)
        return result

    async def request(self, key: str, model: Any) -> dict[str, Any]:
        if (
            not isinstance(model, str)
            or len(model) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in model)
        ):
            raise APIError(
                400, "Model must be a name of at most 200 characters, without control characters."
            )
        model = model.strip()
        _, sup, rt, _ = await self.live_plan(key)
        native_id = self.server.history.session_id_for(key)
        previous = self.read(key)
        if previous.get("status") in ACTIVE:
            if previous.get("requested_model") == model:
                return await self.describe(key)
            raise APIError(
                409, "A restart is already pending. Cancel it before changing the model."
            )
        if not await self.draft_free(sup, rt):
            raise APIError(
                409,
                (
                    "There may be unsent text in this terminal. Send it or clear it "
                    "yourself, then retry."
                ),
            )
        if self.plan(key)[1] is not sup:
            raise APIError(409, "The terminal changed. Reopen Restart and try again.")
        if self.server.history.session_id_for(key) != native_id:
            raise APIError(409, "The conversation changed. Reopen Restart and try again.")
        # Another request can arrive while the screen read is in flight.
        if self.read(key).get("status") in ACTIVE:
            raise APIError(409, "A restart is already pending.")
        self.save(
            key,
            id=uuid.uuid4().hex,
            status="queued",
            requested_model=model,
            conversation_id=native_id,
            requested_at=int(time.time() * 1000),
            error=None,
        )
        if self.turn_finished(key):
            self.schedule(key)
        return await self.describe(key)

    def cancel(self, key: str) -> dict[str, Any]:
        if self.running(key):
            raise APIError(409, "Restart has already begun.")
        task = self.tasks.pop(key, None)
        if task:
            task.cancel()
        return self.save(key, status="canceled", error=None)

    def observe(self, event: dict[str, Any]) -> None:
        key = str(event.get("session_key") or "")
        if not key:
            return
        if event.get("event_type") in ACTIVITY:
            self.epochs[key] = self.epochs.get(key, 0) + 1
        if (
            event.get("lifecycle") in {"stopped", "archived", "terminated"}
            and self.read(key).get("status") == "queued"
        ):
            self.cancel(key)

    def hook_finished(self, event: dict[str, Any]) -> None:
        key = str(event.get("session_key") or "")
        if (
            key
            and event.get("event_type") == events.STOP
            and not event.get("agent_id")
            and event.get("stop_hook_active") is not True
            and self.turn_finished(key)
        ):
            self.schedule(key)

    def schedule(self, key: str) -> None:
        if self.closing or self.read(key).get("status") != "queued" or key in self.tasks:
            return
        self.tasks[key] = asyncio.create_task(self.execute(key))

    async def execute(self, key: str) -> None:
        epoch = self.epochs.get(key, 0)
        request_id = self.read(key).get("id")
        previous_model = self.read(key).get("configured_model")
        try:
            _, sup, rt, binary = await self.live_plan(key)
            native_id = self.server.history.session_id_for(key)
            old_version = self.read(key).get("cli_version")
            await cli_version(binary)  # verify the installed CLI responds before stopping
            if (await self.live_plan(key))[1] is not sup:
                raise APIError(409, "The terminal changed while restart was pending.")
            # The Stop hook can return just before the CLI paints its prompt.
            # Wait for that prompt only AFTER positive turn-end evidence.
            for _ in range(20):
                if self.epochs.get(key, 0) != epoch or not self.turn_finished(key):
                    return  # next actual turn end can satisfy the same request
                if await self.draft_free(sup, rt):
                    break
                await asyncio.sleep(0.1)
            else:
                raise APIError(
                    409,
                    (
                        "Restart paused: the prompt isn't empty or couldn't be verified. "
                        "Your input was preserved. Clear the draft and retry."
                    ),
                )
            data = self.read(key)
            if data.get("status") != "queued" or data.get("id") != request_id:
                return
            # Finish blocking probes before the last draft check. Revalidate
            # metadata on-loop, then begin stop (whose tmux kill runs off-loop).
            if self.epochs.get(key, 0) != epoch or not self.turn_finished(key):
                return
            if self.plan(key)[1] is not sup:
                raise APIError(409, "The terminal changed while restart was pending.")
            if self.server.history.session_id_for(key) != native_id:
                raise APIError(
                    409,
                    (
                        "The conversation changed while restart was pending. Retry after "
                        "reviewing the session."
                    ),
                )
            self.save(
                key,
                status="restarting",
                previous_cli_version=old_version,
                configured_model=data.get("requested_model") or data.get("configured_model"),
            )
            self.server._set_lifecycle(key, "stopped")
            await self.server.orchestrator.stop(key)
            self.server.approvals.drop_session(key)
            status, result = await self.server._resume_session(key, exact=True)
            if status != 200:
                raise APIError(status, result["error"])
            await asyncio.sleep(0.3)
            resumed = self.server.orchestrator.get(key)
            if resumed is None or not await asyncio.to_thread(getattr, resumed, "running"):
                raise APIError(
                    409, "The agent exited during restart. Check its terminal before resuming."
                )
            try:
                new_version = await cli_version(binary)
            except (OSError, ValueError, TimeoutError):
                new_version = "Not reported"
            self.save(key, status="completed", cli_version=new_version, error=None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # An invalid model can make the CLI exit at launch. Keep Resume
            # usable with the prior preference instead of trapping the stopped
            # session in a loop of relaunching the rejected model.
            self.save(key, status="failed", error=str(exc), configured_model=previous_model)
        finally:
            if self.tasks.get(key) is asyncio.current_task():
                self.tasks.pop(key, None)
                # A new turn can finish while the old task is yielding. Its
                # hook cannot schedule until this task relinquishes the slot.
                if self.epochs.get(key, 0) != epoch and self.turn_finished(key):
                    self.schedule(key)

    async def close(self) -> None:
        self.closing = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
