"""Durable, exact-conversation restarts. Never infer a turn end from output."""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.core import events
from duckterm.core.session_api import APIError
from duckterm.harnesses import runtime_for
from duckterm.runtimes.base import Harness

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
LAUNCH_BINARIES = {**BINARIES, "copilot": "copilot"}


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
                if data.get("source_harness") == row.get("runtime"):
                    self.save(key, configured_model=data.get("previous_configured_model"))
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

    def common_plan(self, key: str) -> tuple[dict[str, Any], Any, Any]:
        if self.server.archives.pending(key):
            raise APIError(409, "Archive pending; undo it first")
        from duckterm import transfers

        row = self.server.history.session(key)
        if row is None:
            raise APIError(404, "Session not found")
        if row.get("state") == "merged" or self.server.history.fork_merges.closing(key):
            raise APIError(409, "Closed or merging sessions cannot be restarted")
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
        name = str(row.get("runtime") or "generic")
        rt = runtime_for(name, LAUNCH_BINARIES.get(name, "true"))
        cwd = Path(str(row.get("worktree_path") or row.get("cwd") or "."))
        if not cwd.is_dir():
            raise APIError(409, "The session's project folder no longer exists.")
        return row, sup, rt

    def plan(self, key: str, harness: str | None = None) -> tuple[dict[str, Any], Any, Any, str]:
        row, sup, rt = self.common_plan(key)
        current = str(row.get("runtime") or "generic")
        if harness is not None and harness != current:
            binary = LAUNCH_BINARIES.get(harness)
            if not binary:
                raise APIError(400, "Unknown target harness.")
            if not shutil.which(binary):
                raise APIError(409, f"{binary} is not installed on this computer.")
            return row, sup, rt, binary
        binary = BINARIES.get(current)
        if not binary:
            raise APIError(409, "This harness cannot resume an exact conversation for Restart.")
        cwd = Path(str(row.get("worktree_path") or row.get("cwd") or "."))
        sid = self.server.history.session_id_for(key)
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

    async def live_plan(
        self, key: str, harness: str | None = None
    ) -> tuple[dict[str, Any], Any, Any, str]:
        plan = self.plan(key, harness)
        # tmux may wait for the control stream that this event loop drains.
        # Never wait for its subprocess here, including through a property.
        if not await asyncio.to_thread(getattr, plan[1], "running"):
            raise APIError(409, "This terminal is no longer running. Use Resume instead.")
        if self.plan(key, harness)[1] is not plan[1]:
            raise APIError(409, "The terminal changed. Reopen Restart and try again.")
        return plan

    async def switch_version(self, harness: str, binary: str) -> str:
        try:
            version = await cli_version(binary)
        except (OSError, ValueError, TimeoutError) as exc:
            raise APIError(409, f"Could not verify {binary} before switching: {exc}") from exc
        if harness == "codex":
            match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version)
            if not match or tuple(map(int, match.groups())) >= (0, 159, 0):
                raise APIError(
                    409,
                    "Switching to this Codex version requires shared-daemon conversation "
                    "binding, which is not available yet.",
                )
        return version

    async def options(self, key: str) -> dict[str, Any]:
        """Resume proof gates only that path, never discovery of other harnesses."""
        from duckterm.model_catalog import CatalogError

        row = self.server.history.session(key)
        if row is None:
            raise APIError(404, "Session not found")
        current = str(row.get("runtime") or "generic")
        result: dict[str, Any] = {
            "current": {
                "harness": current,
                "model": self.read(key).get("configured_model") or row.get("model") or "",
            },
            "resume_restart": {"available": False},
            "harnesses": [],
        }
        common_error = None
        try:
            _, sup, rt = self.common_plan(key)
            if not await asyncio.to_thread(getattr, sup, "running"):
                raise APIError(409, "This terminal is no longer running. Use Resume instead.")
            if self.common_plan(key)[1] is not sup:
                raise APIError(409, "The terminal changed. Reopen Restart and try again.")
            result["draft_clear"] = await self.draft_free(sup, rt)
            result["after_turn"] = not self.turn_finished(key)
        except APIError as exc:
            common_error = str(exc)
            result["reason"] = common_error
        try:
            if common_error:
                raise APIError(409, common_error)
            self.plan(key)
            result["resume_restart"] = {"available": True}
        except APIError as exc:
            result["resume_restart"]["reason"] = str(exc)

        async def choice(name: str, binary: str) -> dict[str, Any]:
            adapter = runtime_for(name, binary)
            model_supported = type(adapter).model_arguments is not Harness.model_arguments
            entry: dict[str, Any] = {
                "name": name,
                "available": True,
                "models": [],
                "model_source": "unknown",
                "model_selection": {"available": model_supported},
                "watchable": adapter.hook_spec is not None,
                "context": "native" if name == current else "seeded_new_conversation",
            }
            if not model_supported:
                entry["model_selection"]["reason"] = "Model selection not supported."
            reason = common_error
            if not reason and name == current:
                reason = result["resume_restart"].get("reason")
            installed = shutil.which(binary) is not None
            if not reason and not installed:
                reason = f"{binary} is not installed on this computer."
            if not reason and name != current:
                try:
                    entry["cli_version"] = await self.switch_version(name, binary)
                except (APIError, OSError, ValueError, TimeoutError) as exc:
                    reason = str(exc)
            if reason:
                entry.update(available=False, reason=reason)
            if installed and model_supported and not common_error:
                try:
                    entry["models"] = await self.server.model_catalog.choices(name)
                    entry["model_source"] = "harness-reported"
                except CatalogError as exc:
                    entry["model_reason"] = str(exc)
            return entry

        result["harnesses"] = await asyncio.gather(
            *(choice(name, binary) for name, binary in LAUNCH_BINARIES.items())
        )
        return result

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
        # A seeded switch can learn its initial ID from a process-owned parent
        # Stop. It still needs positive turn evidence, never a daemon guess.
        learned_switch_id = (
            requested.get("context") == "seeded_new_conversation"
            and requested.get("conversation_id") is None
            and last.get("runtime") == requested.get("source_harness")
            and last.get("hook_host") != "daemon"
        )
        if (
            requested.get("status") == "queued"
            and requested.get("conversation_id") != sid
            and not learned_switch_id
        ):
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

    async def request(self, key: str, model: Any, harness: Any = None) -> dict[str, Any]:
        if (
            not isinstance(model, str)
            or len(model) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in model)
        ):
            raise APIError(
                400, "Model must be a name of at most 200 characters, without control characters."
            )
        model = model.strip()
        if harness is not None and (not isinstance(harness, str) or harness not in LAUNCH_BINARIES):
            raise APIError(400, "Unknown target harness.")
        row, sup, rt, binary = await self.live_plan(key, harness)
        target = harness or str(row.get("runtime") or "generic")
        switching = target != row.get("runtime")
        if switching:
            await self.switch_version(target, binary)
        if model:
            runtime_for(target, binary).model_arguments(model)
        native_id = self.server.history.session_id_for(key)
        previous = self.read(key)
        if previous.get("status") in ACTIVE:
            if (
                previous.get("requested_model") == model
                and previous.get("requested_harness", row.get("runtime")) == target
            ):
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
        current_plan = self.plan(key, harness)
        if current_plan[1] is not sup or current_plan[0].get("runtime") != row.get("runtime"):
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
            requested_harness=target,
            source_harness=row.get("runtime"),
            context="seeded_new_conversation" if switching else "native",
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
        harness = self.read(key).get("requested_harness")
        switched = False
        try:
            row, sup, rt, binary = await self.live_plan(key, harness)
            if self.read(key).get("source_harness", row.get("runtime")) != row.get("runtime"):
                raise APIError(409, "The harness changed while restart was pending.")
            switching = harness is not None and harness != row.get("runtime")
            native_id = self.server.history.session_id_for(key)
            old_version = self.read(key).get("cli_version")
            if switching:
                assert isinstance(harness, str)
                await self.switch_version(harness, binary)
            else:
                await cli_version(binary)  # verify before stopping
            if (await self.live_plan(key, harness))[1] is not sup:
                raise APIError(409, "The terminal changed while restart was pending.")
            prepared = None
            if switching:
                from duckterm.harness_switch import prepare

                prepared = await prepare(self.server, key, row)
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
            if self.plan(key, harness)[1] is not sup:
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
                previous_configured_model=previous_model,
                configured_model=(
                    data.get("requested_model")
                    if switching
                    else data.get("requested_model") or data.get("configured_model")
                ),
            )
            self.server._set_lifecycle(key, "stopped")
            await self.server.orchestrator.stop(key)
            self.server.approvals.drop_session(key)
            if switching:
                from duckterm.harness_switch import launch

                assert prepared is not None and isinstance(harness, str)
                switched = True
                await launch(
                    self.server, key, harness, binary, data.get("requested_model") or "", prepared
                )
            else:
                status, result = await self.server._resume_session(key, exact=True)
                if status != 200:
                    raise APIError(status, result["error"])
            await asyncio.sleep(0.3)
            resumed = self.server.orchestrator.get(key)
            if resumed is None or not await asyncio.to_thread(getattr, resumed, "running"):
                if switched:
                    from duckterm.harness_switch import restore

                    # A dead process may still have output and SessionEnd pending.
                    # Drain its supervisor before restoring the old harness identity.
                    await self.server.orchestrator.stop(key)
                    restore(self.server, key)
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
            self.save(
                key,
                status="failed",
                error=str(exc),
                configured_model=(
                    self.read(key).get("configured_model") if switched else previous_model
                ),
            )
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
