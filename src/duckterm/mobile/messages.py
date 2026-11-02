"""Optional phone conversations: scoped, durable, target-bound terminal delivery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.mobile import delivery
from duckterm.mobile.grants import Denied, Grants
from duckterm.mobile.message_store import MessageError, MessageStore, payload_hash, receipt

if TYPE_CHECKING:
    from duckterm.core.orchestrator import SessionSupervisor
    from duckterm.server import Server

CONTEXT_SECONDS = 60
QUIET_MS = 120000


def validate(request: object) -> dict[str, str]:
    if (
        not isinstance(request, dict)
        or set(request) != {"session_id", "send_context", "request_id", "text"}
        or not all(isinstance(value, str) for value in request.values())
    ):
        raise MessageError(400, "invalid_message")
    if len(request["session_id"]) > 256 or not request["session_id"]:
        raise MessageError(400, "invalid_session")
    if len(request["send_context"]) > 256:
        raise MessageError(400, "invalid_context")
    try:
        if str(uuid.UUID(request["request_id"])) != request["request_id"]:
            raise ValueError()
    except ValueError as exc:
        raise MessageError(400, "invalid_request_id") from exc
    text = request["text"].replace("\r\n", "\n")
    try:
        length = len(text.encode())
    except UnicodeEncodeError as exc:
        raise MessageError(400, "invalid_text") from exc
    if not text.strip() or any(
        (ord(char) < 32 and char not in "\n\t") or 127 <= ord(char) < 160 for char in text
    ):
        raise MessageError(400, "invalid_text")
    if length > 4096:
        raise MessageError(413, "message_too_large")
    return {**request, "text": text}


class Messages:
    def __init__(self, server: Server, directory: Path) -> None:
        self.server = server
        self.directory = directory
        self.grants = Grants(directory)
        self.store = MessageStore(directory)
        self.secret = secrets.token_bytes(32)
        self.bindings: dict[str, tuple[object, str, str]] = {}
        self.delivered_at: dict[str, float] = {}
        self.initialized = False
        self.initialize_lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self.closed = False

    def enabled(self) -> bool:
        return not self.closed and (self.directory / "ducky-bridge.json").is_file()

    def target(self, key: str) -> tuple[SessionSupervisor | None, str | None]:
        row = self.server.history.session(key)
        if row is None or row.get("state") in {"archived", "merged"}:
            return None, "session_unavailable"
        if row.get("state") not in {"idle", "busy", "waiting"}:
            return None, "session_finished"
        if row.get("runtime") not in {"claude-code", "codex"}:
            return None, "unsupported_harness"
        sup = self.server.orchestrator.get(key)
        if (
            sup is None
            or not sup._tmux_target
            or sup._preparing
            or (sup._preparation is not None and not sup._preparation.allow_automation)
        ):
            return None, "terminal_unavailable"
        if self.server.restarts.pending(key):
            return None, "restart_pending"
        return sup, None

    def configuration(self) -> tuple[int, int] | None:
        try:
            info = (self.directory / "ducky-bridge.json").stat()
            return info.st_ino, info.st_mtime_ns
        except OSError:
            return None

    def binding(self, key: str, sup: SessionSupervisor) -> str:
        # Hold the supervisor itself so Python object-ID reuse cannot revive a
        # previous terminal's authorization. Re-adoption/restart rotates this.
        signature = repr(
            (
                sup._tmux_target,
                sup._primary_fd,
                id(sup._proc),
                self.server.history.native_identity(key),
                self.configuration(),
            )
        )
        old = self.bindings.get(key)
        if old is None or old[0] is not sup or old[1] != signature:
            self.bindings[key] = (sup, signature, secrets.token_hex(16))
        return self.bindings[key][2]

    def context(self, key: str, binding: str, timestamp: int) -> str:
        digest = hmac.new(self.secret, f"{key}:{binding}:{timestamp}".encode(), hashlib.sha256)
        return f"{timestamp}.{digest.hexdigest()}"

    def fields(self, key: str, device: dict[str, Any]) -> dict[str, Any]:
        if "session:send" not in device.get("capabilities", []):
            return {"can_send": False, "send_context": None, "send_reason": "permission_required"}
        sup, reason = self.target(key)
        if sup is None or not self.enabled():
            return {"can_send": False, "send_context": None, "send_reason": reason or "disabled"}
        return {
            "can_send": True,
            "send_context": self.context(key, self.binding(key, sup), int(time.time())),
            "send_reason": None,
        }

    async def ensure_store(self) -> None:
        async with self.initialize_lock:
            if not self.initialized:
                await asyncio.to_thread(self.store.initialize)
                self.initialized = True

    async def start(self) -> None:
        if self.store.path.exists():
            await self.ensure_store()
            self.ensure_worker()

    def ensure_worker(self) -> None:
        if not self.closed and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.run())

    async def accept(self, device: dict[str, Any], raw: object) -> dict[str, Any]:
        request = validate(raw)
        await self.ensure_store()
        existing = await asyncio.to_thread(self.store.find, device["id"], request["request_id"])
        digest = payload_hash(request)
        if existing is not None:
            if existing["payload_hash"] != digest:
                raise MessageError(409, "request_conflict")
            return receipt(existing)
        key = request["session_id"]
        sup, reason = self.target(key)
        if sup is None:
            raise MessageError(409, reason or "session_unavailable")
        binding = self.binding(key, sup)
        try:
            timestamp = int(request["send_context"].split(".", 1)[0])
        except ValueError as exc:
            raise MessageError(409, "stale_context") from exc
        if not 0 <= time.time() - timestamp <= CONTEXT_SECONDS or not hmac.compare_digest(
            request["send_context"], self.context(key, binding, timestamp)
        ):
            raise MessageError(409, "stale_context")
        try:
            pane = await asyncio.to_thread(delivery.identity, sup._tmux_target or "")
        except Exception as exc:
            raise MessageError(409, "terminal_unavailable") from exc
        current, _ = self.target(key)
        if current is not sup or self.binding(key, sup) != binding:
            raise MessageError(409, "stale_context")
        now = int(time.time() * 1000)
        row = dict(
            device=device["id"],
            request_id=request["request_id"],
            host=device["host_id"],
            session_id=key,
            payload_hash=digest,
            binding=binding,
            pane=pane,
            text=request["text"],
            status="queued",
            reason=None,
            created_at=now,
            updated_at=now,
        )

        def commit() -> dict[str, Any]:
            with self.grants.authorized_device(
                device["id"], "session:send", host_id=device["host_id"]
            ):
                if not self.enabled():
                    raise MessageError(503, "disabled")
                return self.store.insert(row)

        saved = await asyncio.to_thread(commit)
        self.ensure_worker()
        return receipt(saved)

    async def lookup(self, device: dict[str, Any], request_id: str) -> dict[str, Any]:
        if not self.initialized and not self.store.path.exists():
            raise MessageError(404, "request_not_found")
        await self.ensure_store()
        row = await asyncio.to_thread(self.store.find, device["id"], request_id)
        if row is None or row["host"] != device["host_id"]:
            raise MessageError(404, "request_not_found")
        return receipt(row)

    async def receipts(self, device: dict[str, Any], key: str) -> list[dict[str, Any]]:
        if not self.initialized and not self.store.path.exists():
            return []
        await self.ensure_store()
        return await asyncio.to_thread(self.store.for_session, device["id"], key)

    def ready(self, row: dict[str, Any], sup: SessionSupervisor) -> bool:
        current, _ = self.target(row["session_id"])
        session = self.server.history.session(row["session_id"])
        return bool(
            self.enabled()
            and current is sup
            and self.binding(row["session_id"], sup) == row["binding"]
            and session
            and session["state"] == "idle"
            and time.monotonic() - self.delivered_at.get(row["session_id"], 0) >= 2
            and time.time() * 1000 - sup.last_owner_input_ms >= QUIET_MS
            and not any(a.session_key == row["session_id"] for a in self.server.approvals.pending())
        )

    async def attempt(self, row: dict[str, Any]) -> None:
        key = row["session_id"]
        sup, reason = self.target(key)
        if sup is None or self.binding(key, sup) != row["binding"]:
            await asyncio.to_thread(
                self.store.transition, row, "cancelled", reason or "target_changed"
            )
            return
        try:
            await asyncio.to_thread(
                self.grants.authenticated_by_id, row["device"], "session:send", host_id=row["host"]
            )
        except Denied:
            await asyncio.to_thread(self.store.transition, row, "cancelled", "permission_revoked")
            return
        if not self.ready(row, sup):
            return
        target = sup._tmux_target or ""
        last_input = sup.last_owner_input_ms
        configuration = self.configuration()
        loop = asyncio.get_running_loop()

        async def ready_now() -> bool:
            return self.ready(row, sup) and sup.last_owner_input_ms == last_input

        def confirm_ready() -> bool:
            check = asyncio.run_coroutine_threadsafe(ready_now(), loop)
            try:
                return check.result(timeout=1)
            finally:
                if not check.done():
                    check.cancel()

        def write() -> bool:
            with self.grants.authorized_device(row["device"], "session:send", host_id=row["host"]):
                if (
                    not self.enabled()
                    or self.configuration() != configuration
                    or sup._preparing
                    or sup._tmux_target != target
                    or sup.last_owner_input_ms != last_input
                    or delivery.identity(target) != row["pane"]
                ):
                    return False
                screen = delivery.screen(target)
                if (
                    not sup.runtime.prompt_is_empty(screen)
                    or sup.runtime.approval_prompt_visible(screen)
                    or sup.last_owner_input_ms != last_input
                    or not confirm_ready()
                ):
                    return False
                if not self.store.transition(row, "delivering"):
                    return False
                outcome = {**row, "status": "delivering"}
                try:
                    if (
                        not self.enabled()
                        or self.configuration() != configuration
                        or sup.last_owner_input_ms != last_input
                    ):
                        self.store.transition(outcome, "cancelled", "target_changed")
                        return False
                    delivery.write(
                        row["pane"].split(":", 1)[0],
                        b"\x1b[200~" + row["text"].encode() + b"\x1b[201~",
                    )
                    # Preserve the desktop gap proven against Claude Code swallowing Enter.
                    time.sleep(self.server._SUBMIT_GAP_S)
                    if (
                        not self.enabled()
                        or self.configuration() != configuration
                        or sup._preparing
                        or delivery.identity(target) != row["pane"]
                        or sup.last_owner_input_ms != last_input
                        or sup.runtime.approval_prompt_visible(delivery.screen(target))
                        or not confirm_ready()
                    ):
                        raise OSError("Target changed")
                    delivery.write(row["pane"].split(":", 1)[0], b"\r")
                except Exception:
                    self.store.transition(outcome, "unknown", "delivery_uncertain")
                    return False
                self.store.transition(outcome, "delivered")
                # Give hooks/state observation time to report the new agent turn.
                self.delivered_at[key] = time.monotonic()
                return True

        await sup.write_queued_action(write, lambda: self.ready(row, sup))

    async def run(self) -> None:
        while not self.closed:
            try:
                if not self.enabled():
                    await asyncio.to_thread(self.store.cancel, "disabled")
                else:
                    seen: set[str] = set()
                    for row in await asyncio.to_thread(self.store.pending):
                        if row["session_id"] in seen:
                            continue
                        seen.add(row["session_id"])
                        try:
                            await self.attempt(row)
                        except Exception:
                            # One unavailable terminal must not starve other agents.
                            continue
            except Exception:
                # Journal/lock errors defer work; never retry an uncertain write.
                pass
            await asyncio.sleep(1)

    async def disable(self) -> None:
        if self.initialized:
            await asyncio.to_thread(self.store.cancel, "disabled")

    async def close(self) -> None:
        self.closed = True
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.initialized:
            await asyncio.to_thread(self.store.cancel, "server_stopped")
