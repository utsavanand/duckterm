"""Dashboard reads reuse session memory retrieval under existing owner authentication."""

from __future__ import annotations

from typing import TYPE_CHECKING

from duckterm.core.session_api import APIError
from duckterm.helpers import security
from duckterm.transport.httpio import write_json

if TYPE_CHECKING:
    import asyncio

    from duckterm.server import Server


async def handle(
    server: Server,
    writer: asyncio.StreamWriter,
    headers: dict[str, str],
    key: str,
    action: str | None,
    method: str,
    query: dict[str, list[str]],
) -> None:
    if not security.token_valid(headers, server.token):
        await write_json(writer, 401, {"error": "owner token required"})
        return
    if method != "GET":
        await write_json(writer, 405, {"error": "Session Memory is read-only"})
        return
    try:
        result = await server.memory.operate(key, action or "view", query)
        await write_json(writer, 200, result)
    except APIError as exc:
        await write_json(writer, exc.status, {"error": str(exc)})
