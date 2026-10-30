"""Owner-only fork review and merge. Delivery stays in the priority broker."""

import asyncio
import json
import subprocess
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError
from duckterm.helpers import security

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
                close_state = None
                if not row["keep_open"]:
                    from duckterm.merge_closing import finish

                    close_state = await finish(server, key, row["parent"])
                else:
                    merges.finish(row)
                server._oracle_soon()
                result = merges.status(key, row["id"], server._can_pin)
                if close_state:
                    result["closeState"] = close_state
        await _write_json(writer, 200, result)
    except (APIError, ValueError, OSError) as exc:
        status = 400 if isinstance(exc, ValueError) else 500
        if isinstance(exc, APIError):
            status = exc.status
        await _write_json(writer, status, {"error": str(exc)})
