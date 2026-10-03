"""Owner-only fork review and merge. Delivery stays in the priority broker."""

import asyncio
import json
import subprocess
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError
from duckterm.helpers import security
from duckterm.runtimes.base import AT_REST_STATES

if TYPE_CHECKING:
    from duckterm.server import Server


def commit_at(path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


async def handle(
    server: "Server",
    writer: asyncio.StreamWriter,
    headers: dict[str, str],
    key: str,
    method: str,
    body: bytes,
) -> None:
    from duckterm.transport.httpio import write_json as _write_json

    if not security.token_valid(headers, server.token):
        await _write_json(writer, 401, {"error": "owner credential required"})
        return
    result: dict[str, Any]
    merges = server.history.fork_merges
    try:
        if method == "LIST":
            result = {"merges": merges.list(key, server._can_pin)}
        elif method == "GET":
            result = merges.preview(key, server._can_pin)
            row = server.history.session(key) or {}
            if row.get("worktree_path"):
                commit = await asyncio.to_thread(commit_at, row["worktree_path"])
                result["child"]["commit"] = commit
                result["summary"] += "\nCommit: " + (commit or "Unavailable")
        else:
            request = json.loads(body or b"{}")
            if not isinstance(request, dict):
                raise APIError(400, "Expected a JSON object")
            async with server._merge_lock:
                if server.archives.pending(key) or server.restarts.read(key).get("status") in (
                    "queued",
                    "restarting",
                ):
                    raise APIError(
                        409, "Finish or cancel the pending archive/restart before merging"
                    )
                row = merges.prepare(key, request, server._can_pin)
                if not row["keep_open"]:
                    child = server.history.session(key)
                    if child is None:
                        raise APIError(409, "Child deleted after the summary was saved")
                    if child["state"] != "merged":
                        sup = server.orchestrator.get(key)
                        if sup is not None:
                            await server.orchestrator.stop(key)
                        elif child["state"] not in AT_REST_STATES:
                            raise APIError(
                                409,
                                "Summary saved, but the child terminal could not be stopped. "
                                "Retry to finish closing it.",
                            )
                        merges.finish(row)
                        server._set_lifecycle(key, "merged")
                        server.approvals.drop_session(key)
                else:
                    merges.finish(row)
                server._oracle_soon()
                result = merges.status(key, row["id"], server._can_pin)
        await _write_json(writer, 200, result)
    except (APIError, ValueError, OSError) as exc:
        status = 400 if isinstance(exc, ValueError) else 500
        if isinstance(exc, APIError):
            status = exc.status
        await _write_json(writer, status, {"error": str(exc)})
