"""Explicitly scoped phone analysis, admitted quickly and generated off-request."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

from duckterm.core import fleet_questions as fleet
from duckterm.llm.summarizer import Summary
from duckterm.mobile.analysis_store import WORK_MS, AnalysisError, AnalysisStore, receipt
from duckterm.mobile.grants import Denied, Grants

if TYPE_CHECKING:
    from duckterm.server import Server

__all__ = ["Analysis", "AnalysisError"]

P = ParamSpec("P")
T = TypeVar("T")
CONTEXT_MS = 60000
MAX_ANSWER_BYTES = 32768
SWEEP_SECONDS = 30.0
CHECK_SECONDS = 1.0
GRANT_WAIT_SECONDS = 0.1


class _GrantBusy(AnalysisError):
    def __init__(self) -> None:
        super().__init__(503, "analysis_unavailable")


def _now() -> int:
    return int(time.time() * 1000)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _uuid(value: object) -> str:
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError as exc:
        raise AnalysisError(400, "invalid_request_id") from exc
    return value


def _question(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != {"request_id", "context_id", "question"}:
        raise AnalysisError(400, "invalid_request")
    request_id = _uuid(raw["request_id"])
    context_id, question = raw["context_id"], raw["question"]
    if (
        not isinstance(context_id, str)
        or not context_id
        or len(context_id) > 128
        or not context_id.isascii()
        or not all(c.isalnum() or c in "_-" for c in context_id)
    ):
        raise AnalysisError(400, "invalid_context")
    if (
        not isinstance(question, str)
        or not question.strip()
        or any((ord(c) < 32 and c not in "\n\t") or 127 <= ord(c) < 160 for c in question)
    ):
        raise AnalysisError(400, "invalid_question")
    try:
        length = len(question.encode())
    except UnicodeEncodeError as exc:
        raise AnalysisError(400, "invalid_question") from exc
    if length > 4096:
        raise AnalysisError(413, "question_too_large")
    return {"request_id": request_id, "context_id": context_id, "question": question}


@dataclass(frozen=True)
class Context:
    row: dict[str, Any]
    expires_at: int
    deadline: float


class _ProviderPool:
    """A cancelled asyncio waiter cannot release real underlying provider capacity."""

    def __init__(self) -> None:
        self.slots = threading.BoundedSemaphore(2)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ducky-analysis")

    def submit(self, prepared: fleet.Prepared) -> asyncio.Future[Summary] | None:
        if not self.slots.acquire(blocking=False):
            return None
        try:
            future = self.executor.submit(fleet.generate, prepared)
        except BaseException:
            self.slots.release()
            raise
        future.add_done_callback(lambda _: self.slots.release())
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return wrapped


# Preserve the same host's two-call limit even if a service is closed and rebuilt
# while an uncooperative provider thread is still returning. Entries are weak;
# outstanding provider callbacks retain their pool until the real call finishes.
_POOLS: weakref.WeakValueDictionary[str, _ProviderPool] = weakref.WeakValueDictionary()


class Analysis:
    def __init__(self, server: Server, directory: Path) -> None:
        self.server, self.directory = server, directory
        self.grants = Grants(directory)
        self.store = AnalysisStore(directory)
        self.contexts: dict[str, Context] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.sweeper: asyncio.Task[None] | None = None
        self.deadlines: dict[tuple[str, str, str], float] = {}
        self.initialized = False
        self.storage_failed = False
        self.closed = False
        self.signature: str | None = None
        self.pool: _ProviderPool | None = None

    def _store(self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        if self.storage_failed:
            raise AnalysisError(503, "storage_unavailable")
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.storage_failed = True
            raise AnalysisError(503, "storage_unavailable") from exc

    def _ensure_store(self) -> None:
        if self.storage_failed:
            raise AnalysisError(503, "storage_unavailable")
        if not self.initialized:
            self._store(self.store.initialize, _now())
            self.initialized = True
        if self.sweeper is None:
            self.sweeper = asyncio.create_task(self._sweep())

    async def _sweep(self) -> None:
        try:
            while not self.closed:
                await asyncio.sleep(SWEEP_SECONDS)
                self._store(self.store.sweep, _now())
        except AnalysisError:
            return  # Fail closed until the service is restarted.

    def _configuration(self) -> str:
        if self.closed:
            raise AnalysisError(503, "disabled")
        values: list[tuple[int, int, int, int, int] | None] = []
        for name in ("ducky-bridge.json", "ducky-cert.pem", "ducky-key.pem"):
            try:
                info = (self.directory / name).lstat()
            except FileNotFoundError:
                if name == "ducky-bridge.json":
                    raise AnalysisError(503, "disabled") from None
                values.append(None)
                continue
            except OSError as exc:
                raise AnalysisError(503, "analysis_unavailable") from exc
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise AnalysisError(503, "analysis_unavailable")
            values.append(
                (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            )
        signature = hashlib.sha256(_json(values).encode()).hexdigest()
        if self.signature is not None and self.signature != signature:
            self.contexts.clear()
            if self.initialized:
                self._store(
                    self.store.invalidate, _now(), reason="configuration_changed", completed=True
                )
        self.signature = signature
        return signature

    def effective_access(self, device: dict[str, Any]) -> dict[str, bool]:
        capabilities = device.get("capabilities", [])
        enabled = not self.closed and device.get("status") == "active"
        return {
            "folder_questions": enabled
            and "analysis:folder" in capabilities
            and self.server.edition.includes("folder_chat"),
            "oracle_questions": enabled
            and "analysis:oracle" in capabilities
            and self.server.edition.includes("oracle"),
        }

    @staticmethod
    def _scope(raw: object) -> fleet.Scope:
        try:
            return fleet.Scope.parse(raw)
        except (ValueError, UnicodeError) as exc:
            raise AnalysisError(400, "invalid_scope") from exc

    @contextlib.contextmanager
    def _authorized(
        self, device: dict[str, Any], scope: fleet.Scope, revision: int | None = None
    ) -> Iterator[dict[str, Any]]:
        required = "analysis:oracle" if scope.kind == "oracle" else "analysis:folder"
        entered = False
        try:
            with self.grants.authorized_device(
                device["id"], required, host_id=device["host_id"], lock_timeout=0
            ) as live:
                entered = True
                field = "oracle_questions" if scope.kind == "oracle" else "folder_questions"
                if not self.effective_access(live)[field]:
                    raise AnalysisError(403, "permission_required")
                expected = device.get("authorization_revision", 0) if revision is None else revision
                if live.get("authorization_revision", 0) != expected:
                    raise AnalysisError(403, "authorization_changed")
                yield live
        except Denied as exc:
            raise AnalysisError(403, "permission_required") from exc
        except TimeoutError as exc:
            if not entered:
                raise _GrantBusy() from exc
            raise AnalysisError(503, "analysis_unavailable") from exc
        except (OSError, ValueError, KeyError) as exc:
            raise AnalysisError(503, "analysis_unavailable") from exc

    async def _retry_grant(self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Only retry contention, before the protected operation has begun."""
        deadline = asyncio.get_running_loop().time() + GRANT_WAIT_SECONDS
        while True:
            try:
                return function(*args, **kwargs)
            except _GrantBusy:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise
                await asyncio.sleep(min(0.01, remaining))

    def _binding(self, scope: fleet.Scope) -> str:
        try:
            return fleet.binding(self.server, scope)
        except (OSError, ValueError, KeyError) as exc:
            raise AnalysisError(409, "scope_unavailable") from exc

    def _check(self, row: dict[str, Any], capture: fleet.Capture | None = None) -> None:
        scope = self._scope(json.loads(row["scope"]))
        if (
            self._configuration() != row["configuration"]
            or row.get("reason") == "configuration_changed"
        ):
            raise AnalysisError(409, "configuration_changed")
        device = {"id": row["device"], "host_id": row["host"]}
        with self._authorized(device, scope, row["revision"]):
            binding = self._binding(scope)
            if binding != row["binding"] or (
                capture is not None
                and not fleet.current(self.server, capture, observed_binding=binding)
            ):
                raise AnalysisError(409, "scope_changed")

    def _response(self, row: dict[str, Any]) -> dict[str, Any]:
        return {"version": 1, "host_id": row["host"], "receipt": receipt(row)}

    async def start(self) -> None:
        if self.store.path.exists():
            self._ensure_store()  # Interrupt old work; never replay paid inference.
        self._configuration()

    async def context(self, device: dict[str, Any], payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"scope"}:
            raise AnalysisError(400, "invalid_request")
        scope = self._scope(payload["scope"])
        with self._authorized(device, scope) as live:
            configuration = self._configuration()
            now, clock = _now(), asyncio.get_running_loop().time()
            self.contexts = {
                key: value
                for key, value in self.contexts.items()
                if value.expires_at > now and value.deadline > clock
            }
            if (
                len(self.contexts) >= 128
                or sum(
                    value.row["device"] == device["id"] and value.row["host"] == device["host_id"]
                    for value in self.contexts.values()
                )
                >= 4
            ):
                raise AnalysisError(429, "context_limit")
            row = {
                "host": device["host_id"],
                "device": device["id"],
                "scope": _json(scope.json()),
                "binding": self._binding(scope),
                "configuration": configuration,
                "revision": live.get("authorization_revision", 0),
            }
            self._ensure_store()
            history = self._store(self.store.history, row, now)
            pending = self._store(self.store.pending, row, now)
            identifier = secrets.token_urlsafe(32)
            self.contexts[identifier] = Context(row, now + CONTEXT_MS, clock + CONTEXT_MS / 1000)
            return {
                "version": 1,
                "host_id": device["host_id"],
                "scope": scope.json(),
                "context_id": identifier,
                "expires_at": now + CONTEXT_MS,
                "history": history,
                "pending": pending,
            }

    async def accept(self, device: dict[str, Any], payload: object) -> dict[str, Any]:
        request = _question(payload)
        self._configuration()
        self._ensure_store()
        digest = hashlib.sha256(_json(request).encode()).hexdigest()
        old = self._store(
            self.store.find, device["host_id"], device["id"], request["request_id"], _now()
        )
        if old is not None:
            await self._retry_grant(self._check, old)
            if old["payload_hash"] != digest:
                raise AnalysisError(409, "request_conflict")
            return self._response(old)
        context = self.contexts.get(request["context_id"])
        if (
            context is None
            or context.expires_at <= _now()
            or context.deadline <= asyncio.get_running_loop().time()
            or context.row["host"] != device["host_id"]
            or context.row["device"] != device["id"]
        ):
            raise AnalysisError(409, "stale_context")
        self._check(context.row)
        scope = self._scope(json.loads(context.row["scope"]))
        now = _now()
        row = {
            **context.row,
            **request,
            "payload_hash": digest,
            "status": "queued",
            "reason": None,
            "answer": None,
            "created_at": now,
            "updated_at": now,
            "expires_at": now + WORK_MS,
            "captured_at": None,
            "source_count": 0,
            "truncated": 0,
        }
        with self._authorized(device, scope, row["revision"]):
            self._check_identity(row)
            saved = self._store(self.store.insert, row)
        self.deadlines[self._key(saved)] = asyncio.get_running_loop().time() + WORK_MS / 1000
        self._kick()
        return self._response(saved)

    def _check_identity(self, row: dict[str, Any], capture: fleet.Capture | None = None) -> None:
        # Used inside the already-held grant lock: never recursively acquire it.
        scope = self._scope(json.loads(row["scope"]))
        if self._configuration() != row["configuration"]:
            raise AnalysisError(409, "configuration_changed")
        binding = self._binding(scope)
        if binding != row["binding"] or (
            capture is not None
            and not fleet.current(self.server, capture, observed_binding=binding)
        ):
            raise AnalysisError(409, "scope_changed")

    async def lookup(self, device: dict[str, Any], request_id: str) -> dict[str, Any]:
        identifier = _uuid(request_id)
        self._configuration()
        self._ensure_store()
        row = self._store(self.store.find, device["host_id"], device["id"], identifier, _now())
        if row is None:
            raise AnalysisError(404, "request_not_found")
        await self._retry_grant(self._check, row)
        return self._response(row)

    @staticmethod
    def _key(row: dict[str, Any]) -> tuple[str, str, str]:
        return row["host"], row["device"], row["request_id"]

    def _kick(self) -> None:
        if self.closed or self.storage_failed:
            return
        try:
            queued = self._store(self.store.queued, _now())
        except AnalysisError:
            return
        while queued and len(self.tasks) < 2:
            task = asyncio.create_task(self._worker())
            self.tasks.add(task)
            task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Provider diagnostics never enter logs.
        self._kick()

    async def _worker(self) -> None:
        while not self.closed and not self.storage_failed:
            try:
                queued = self._store(self.store.queued, _now())
                if not queued:
                    return
                row = queued[0]
                await self._run(row)
            except AnalysisError:
                return

    def _check_work(self, row: dict[str, Any], capture: fleet.Capture | None = None) -> None:
        if self.storage_failed:
            raise AnalysisError(503, "storage_unavailable")
        if _now() >= row["expires_at"] or asyncio.get_running_loop().time() >= self.deadlines.get(
            self._key(row), float("inf")
        ):
            raise AnalysisError(409, "work_expired")
        self._check(row, capture)

    def _poll_delay(self, row: dict[str, Any]) -> float:
        """Poll cheaply while waiting, without moving either expiry deadline."""
        return max(
            0.0,
            min(
                CHECK_SECONDS,
                (float(row["expires_at"]) - _now()) / 1000,
                self.deadlines.get(self._key(row), float("inf"))
                - asyncio.get_running_loop().time(),
            ),
        )

    def _terminal(self, row: dict[str, Any], status: str, reason: str) -> None:
        with contextlib.suppress(AnalysisError):
            self._store(
                self.store.transition,
                row,
                status,
                _now(),
                reason=reason,
                captured_at=row.get("captured_at"),
                source_count=row["source_count"],
                truncated=bool(row["truncated"]),
            )

    async def _run(self, row: dict[str, Any]) -> None:
        future: asyncio.Future[Summary] | None = None
        try:
            await self._retry_grant(self._check_work, row)
            scope = self._scope(json.loads(row["scope"]))
            capture = fleet.capture(self.server, scope)
            if capture.binding != row["binding"]:
                raise AnalysisError(409, "scope_changed")
            row["captured_at"] = max(_now(), row["created_at"])
            row["source_count"] = len(capture.rows)
            row["truncated"] = int(capture.truncated)
            if not self._store(
                self.store.transition,
                row,
                "running",
                _now(),
                captured_at=row["captured_at"],
                source_count=row["source_count"],
                truncated=capture.truncated,
            ):
                return
            row["status"] = "running"
            history = self._store(self.store.history, row, _now())
            prior = [{"q": item["question"], "a": item["answer"]} for item in history]

            async def check() -> None:
                await self._retry_grant(self._check_work, row, capture)

            remaining = min((row["expires_at"] - _now()) / 1000, WORK_MS / 1000)
            prepared = await asyncio.wait_for(
                fleet.prepare(self.server, capture, row["question"], prior, check), remaining
            )
            await check()
            if prepared.empty_answer is not None:
                result = fleet.generate(prepared)
            else:
                if self.pool is None:
                    key = str(self.directory.resolve())
                    self.pool = _POOLS.get(key) or _ProviderPool()
                    _POOLS[key] = self.pool
                device = {"id": row["device"], "host_id": row["host"]}

                def submit() -> asyncio.Future[Summary] | None:
                    self._check_work(row, capture)
                    with self._authorized(device, scope, row["revision"]):
                        self._check_identity(row, capture)
                        assert self.pool is not None
                        return self.pool.submit(prepared)

                while future is None:
                    future = await self._retry_grant(submit)
                    if future is None:
                        await asyncio.sleep(self._poll_delay(row))
                while not future.done():
                    await asyncio.wait({future}, timeout=self._poll_delay(row))
                    await check()
                result = future.result()
            await check()
            if not result.text:
                raise AnalysisError(503, "provider_unavailable")
            answer = fleet.clip(result.text, MAX_ANSWER_BYTES)
            device = {"id": row["device"], "host_id": row["host"]}

            def publish() -> None:
                self._check_work(row, capture)
                with self._authorized(device, scope, row["revision"]):
                    self._check_identity(row, capture)
                    self._store(
                        self.store.transition,
                        row,
                        "complete",
                        _now(),
                        answer=answer,
                        captured_at=row["captured_at"],
                        source_count=row["source_count"],
                        truncated=prepared.truncated or answer != result.text,
                    )

            await self._retry_grant(publish)
        except asyncio.CancelledError:
            self._terminal(row, "cancelled", "cancelled")
            raise
        except (AnalysisError, TimeoutError) as exc:
            reason = exc.reason if isinstance(exc, AnalysisError) else "work_expired"
            if reason in {"permission_required", "authorization_changed"}:
                reason = "permission_changed"
            if reason == "disabled":
                reason = "configuration_changed"
            if reason == "scope_unavailable":
                reason = "scope_changed"
            status = (
                "expired"
                if reason == "work_expired"
                else (
                    "failed"
                    if reason
                    in {"provider_unavailable", "storage_unavailable", "analysis_unavailable"}
                    else "cancelled"
                )
            )
            self._terminal(row, status, reason)
        except Exception:
            self._terminal(row, "failed", "provider_failed")
        finally:
            self.deadlines.pop(self._key(row), None)
            task = asyncio.current_task()
            if future is not None and not future.done() and not (task and task.cancelling()):
                # No replacement job gets this worker until the underlying call
                # actually returns. close() may cancel this waiter, but the pool's
                # real-call semaphore still cannot be released prematurely.
                with contextlib.suppress(Exception):
                    await asyncio.shield(future)

    async def disable(self) -> None:
        self.contexts.clear()
        self.signature = None
        if self.initialized or self.store.path.exists():
            self._ensure_store()
            self._store(
                self.store.invalidate, _now(), reason="configuration_changed", completed=True
            )

    async def close(self) -> None:
        self.closed = True
        self.contexts.clear()
        if self.initialized:
            with contextlib.suppress(AnalysisError):
                self._store(self.store.invalidate, _now(), reason="cancelled", completed=False)
        tasks = (*self.tasks, *((self.sweeper,) if self.sweeper is not None else ()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.deadlines.clear()
