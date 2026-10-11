"""Owner-chosen transcript recovery, scoped to one session on this server."""

from __future__ import annotations

import asyncio
import copy
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm import conversation_files
from duckterm.agents import hooks_install
from duckterm.core.session_api import APIError
from duckterm.harnesses import REGISTRY, runtime_for
from duckterm.helpers import security
from duckterm.persistence import native_identity

if TYPE_CHECKING:
    from duckterm.server import Server

SUPPORTED = {"claude-code", "codex"}


def hook_status(runtime: str, cwd: str) -> dict[str, Any]:
    spec = getattr(REGISTRY.get(runtime), "hook_spec", None)
    if not spec:
        return {"status": "unknown", "canInstall": False}
    try:
        for global_scope in (True, False):
            path = spec.path(global_scope=global_scope, project_dir=Path(cwd))
            if not path.exists():
                continue
            if path.stat().st_size > 1024 * 1024:
                return {"status": "unknown", "canInstall": False}
            config = json.loads(path.read_text())
            if not isinstance(config, dict):
                return {"status": "unknown", "canInstall": False}
            expected = spec.build(
                copy.deepcopy(config), str(hooks_install.hook_script_path()), runtime
            )
            if config == expected:
                return {"status": "configured", "canInstall": False}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"status": "unknown", "canInstall": False}
    return {"status": "missing", "canInstall": True}


def transcript_present(runtime: str, cwd: str, native_id: str) -> bool:
    if runtime == "copilot":
        copilot_path = Path.home() / ".copilot/session-store.db"
        if not copilot_path.is_file():
            return False
        with sqlite3.connect(copilot_path.as_uri() + "?mode=ro", uri=True, timeout=1) as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM turns WHERE session_id=? LIMIT 1", (native_id,)
                ).fetchone()
                is not None
            )
    rt = runtime_for(runtime, {"claude-code": "claude", "codex": "codex"}.get(runtime, "true"))
    path = rt.locate_transcript(cwd=Path(cwd), session_id=native_id)
    return bool(
        path
        and conversation_files.excerpts(
            path, runtime, str(Path(cwd).resolve()), False, expected_native_id=native_id
        )
    )


class ConversationRecovery:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.lists: dict[str, dict[str, Any]] = {}
        self.hook_installs: dict[str, asyncio.Lock] = {}

    def row(self, key: str) -> dict[str, Any]:
        row = self.server.history.session(key)
        if row is None:
            raise APIError(404, "Session not found")
        return row

    async def quiescent(self, row: dict[str, Any], require_directory: bool = True) -> bool:
        key = row["session_key"]
        directory = row.get("worktree_path") or row.get("cwd")
        if row.get("state") not in {"stopped", "interrupted", "terminated"}:
            return False
        if require_directory and (
            not isinstance(directory, str)
            or not Path(directory).is_absolute()
            or not await asyncio.to_thread(Path(directory).is_dir)
        ):
            return False
        from duckterm import transfers

        supervisor = self.server.orchestrator.get(key)
        if supervisor is not None and await asyncio.to_thread(getattr, supervisor, "running"):
            return False
        return bool(
            self.server.orchestrator.get(key) is supervisor
            and (row.get("launched") or row.get("heartbeat"))
            and not self.server.archives.pending(key)
            and not self.server.restarts.pending(key)
            and not self.server.history.fork_merges.closing(key)
            and key not in self.server._transfer_sources
            and not transfers.session_transfer(key)
        )

    async def eligible(self, row: dict[str, Any]) -> bool:
        return bool(
            row.get("state") in {"stopped", "interrupted"}
            and row.get("runtime") in SUPPORTED
            and self.server.history.native_identity(row["session_key"])["status"] == "missing"
            and await self.quiescent(row)
        )

    async def identity(self, key: str) -> dict[str, Any]:
        row = self.row(key)
        value = self.server.history.native_identity(key)
        runtime = str(row.get("runtime") or "generic")
        cwd = str(row.get("worktree_path") or row.get("cwd") or ".")
        before = native_identity.revision(row)
        hooks = await asyncio.to_thread(hook_status, runtime, cwd)
        transcript = "unknown"
        if value["native_id"] and Path(cwd).is_absolute():
            try:
                present = await asyncio.to_thread(
                    transcript_present, runtime, cwd, value["native_id"]
                )
                transcript = "present" if present else "missing"
            except (OSError, ValueError, sqlite3.Error):
                transcript = "unavailable"
        if native_identity.revision(self.row(key)) != before:
            raise APIError(409, "Session changed while checking recovery; try again")
        result = {
            "revision": before,
            "canDetach": value["source"] == "adopted" and await self.quiescent(row, False),
            "status": value["status"],
            "source": value["source"],
            "transcript": transcript,
            "hooks": hooks,
            "canAdopt": await self.eligible(row),
            "canResume": value["status"] == "recorded"
            and transcript == "present"
            and await self.quiescent(row),
        }
        directory = row.get("worktree_path") or row.get("cwd")
        if not directory or not Path(directory).is_absolute():
            result["canResume"] = False
            result["reason"] = "This session has no recorded absolute project directory."
        elif runtime not in SUPPORTED and value["status"] == "missing":
            result["reason"] = "This harness does not provide project-scoped transcript recovery."
        if native_identity.revision(self.row(key)) != before:
            raise APIError(409, "Session changed while checking recovery; try again")
        return result

    async def list_candidates(self, key: str) -> dict[str, Any]:
        row = self.row(key)
        if not await self.eligible(row):
            raise APIError(
                409,
                "Recovery requires a stopped or interrupted session without an identity",
            )
        snapshot = native_identity.revision(row)
        cwd = str(Path(row.get("worktree_path") or row.get("cwd") or ".").resolve())
        values, more = await asyncio.to_thread(conversation_files.candidates, row["runtime"], cwd)
        if native_identity.revision(self.row(key)) != snapshot or not await self.eligible(
            self.row(key)
        ):
            raise APIError(409, "Session changed; reopen the picker")
        now = time.monotonic()
        self.lists = {k: v for k, v in self.lists.items() if v["expires"] > now}
        while len(self.lists) >= 32:
            self.lists.pop(next(iter(self.lists)))
        revision = secrets.token_urlsafe(24)
        entries = {secrets.token_urlsafe(24): value for value in values}
        self.lists[revision] = {
            "key": key,
            "snapshot": snapshot,
            "cwd": cwd,
            "runtime": row["runtime"],
            "entries": entries,
            "expires": now + 600,
        }
        return {
            "revision": revision,
            "hasMore": more,
            "candidates": [
                {
                    "handle": handle,
                    "label": f"Conversation {i + 1}",
                    "firstPrompt": item["firstPrompt"],
                    "lastPrompt": item["lastPrompt"],
                    "modifiedAt": item["modifiedAt"],
                    "available": item["available"],
                    "reason": item["reason"],
                }
                for i, (handle, item) in enumerate(entries.items())
            ],
        }

    async def attach(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"revision", "handle"} or not all(
            isinstance(v, str) and len(v) <= 128 for v in body.values()
        ):
            raise APIError(400, "Choose a current conversation handle and revision")
        listing = self.lists.get(body["revision"])
        if not listing or listing["expires"] < time.monotonic() or listing["key"] != key:
            raise APIError(409, "Conversation list expired; reload it")
        candidate = listing["entries"].get(body["handle"])
        if not candidate or not candidate["available"] or not await self.eligible(self.row(key)):
            raise APIError(409, "Conversation choice is no longer available")
        current = await asyncio.to_thread(
            conversation_files.excerpts, Path(candidate["path"]), listing["runtime"], listing["cwd"]
        )
        if not current or current != candidate or not await self.eligible(self.row(key)):
            raise APIError(409, "Transcript or session changed; reload the picker")
        try:
            native_identity.adopt(
                self.server.history._conn, key, listing["snapshot"], current["native_id"]
            )
        except ValueError as exc:
            raise APIError(409, str(exc)) from exc
        self.lists.pop(body["revision"], None)
        return await self.identity(key)

    async def detach(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        if (
            set(body) != {"revision"}
            or not isinstance(body["revision"], str)
            or len(body["revision"]) != 64
        ):
            raise APIError(400, "Current session revision required")
        if not await self.quiescent(self.row(key), False):
            raise APIError(409, "Stop this session before undoing an attachment")
        try:
            native_identity.detach(self.server.history._conn, key, body["revision"])
        except ValueError as exc:
            raise APIError(409, str(exc)) from exc
        return await self.identity(key)

    async def install(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        if body:
            raise APIError(400, "Hook installation takes no path or runtime override")
        row = self.row(key)
        runtime = str(row.get("runtime") or "generic")
        cwd = str(row.get("worktree_path") or row.get("cwd") or ".")
        # Same-runtime installs are serialized; blocking configuration work stays
        # off the event loop. Revalidate the selected session after the lock/read.
        lock = self.hook_installs.setdefault(runtime, asyncio.Lock())
        async with lock:
            snapshot = native_identity.revision(row)
            hooks = await asyncio.to_thread(hook_status, runtime, cwd)
            if native_identity.revision(self.row(key)) != snapshot:
                raise APIError(409, "Session changed; review hook installation again")
            if not hooks["canInstall"]:
                raise APIError(409, "Hooks are configured or their settings need manual review")
            await asyncio.to_thread(
                hooks_install.install, global_scope=True, project_dir=Path(cwd), agent=runtime
            )
        return await self.identity(key)


async def handle(
    server: Server,
    writer: asyncio.StreamWriter,
    headers: dict[str, str],
    key: str,
    operation: str,
    method: str,
    raw: bytes,
) -> None:
    from duckterm.transport.httpio import write_json as _write_json

    if not security.token_valid(headers, server.token):
        await _write_json(writer, 401, {"error": "owner credential required"})
        return
    allowed = {
        "recovery": "GET",
        "candidates": "GET",
        "adopt": "POST",
        "hooks": "POST",
        "detach": "POST",
    }
    if method != allowed.get(operation):
        await _write_json(writer, 405, {"error": "method not allowed"})
        return
    try:
        if len(raw) > 4096:
            raise APIError(413, "Recovery request too large")
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise APIError(400, "Expected an object")
        recovery = server.conversation_recovery
        if operation == "recovery":
            result = await recovery.identity(key)
        elif operation == "candidates":
            result = await recovery.list_candidates(key)
        elif operation == "adopt":
            result = await recovery.attach(key, body)
        elif operation == "detach":
            result = await recovery.detach(key, body)
        else:
            result = await recovery.install(key, body)
        await _write_json(writer, 200, result)
    except APIError as exc:
        await _write_json(writer, exc.status, {"error": str(exc)})
    except (OSError, ValueError, TypeError) as exc:
        await _write_json(writer, 400, {"error": f"Conversation recovery failed: {exc}"})
