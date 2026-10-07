"""Owner-authenticated preparation endpoints for the existing Restart dialog."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError
from duckterm.helpers import security
from duckterm.memory_preparation import PreparationError
from duckterm.transport.httpio import write_json

if TYPE_CHECKING:
    import asyncio

    from duckterm.server import Server


async def handle(
    server: Server,
    writer: asyncio.StreamWriter,
    headers: dict[str, str],
    key: str,
    identity: str | None,
    method: str,
    body: bytes,
    query: dict[str, list[str]],
) -> None:
    if not security.token_valid(headers, server.token):
        await write_json(writer, 401, {"error": "owner token required", "code": "forbidden"})
        return
    try:
        if any(len(v) != 1 for v in query.values()):
            raise APIError(400, "Duplicate query parameter")
        manager = server.memory_preparation
        result: dict[str, Any]
        status = 200
        if method == "POST" and identity is None and not query:
            if len(body) > 8192:
                raise APIError(413, "Preparation request is too large")
            request = json.loads(body or b"{}")
            if not isinstance(request, dict):
                raise ValueError
            result = await manager.start(key, request)
            status = 202
        elif method == "GET" and identity and not set(query) - {"detail", "cursor"}:
            if query.get("detail") == ["full"]:
                result = manager.details(key, identity, query.get("cursor", ["0"])[0])
            elif not query:
                result = manager.status(key, identity)
            else:
                raise APIError(400, "Unknown preparation detail query")
        elif method == "DELETE" and identity and set(query) == {"request_key"}:
            result = manager.cancel(key, identity, query["request_key"][0])
        else:
            raise APIError(405, "Unsupported preparation operation")
        await write_json(writer, status, result)
    except (ValueError, UnicodeError):
        await write_json(writer, 400, {"error": "Invalid preparation request"})
    except APIError as exc:
        await write_json(
            writer,
            exc.status,
            {
                "error": str(exc),
                "code": exc.code if isinstance(exc, PreparationError) else "preparation_failed",
                "process_state": "source_running",
                "retryable": exc.status >= 500,
            },
        )
