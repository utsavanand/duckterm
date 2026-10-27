"""An opt-in TLS listener with its own credentials and a fixed read-only data API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from duckterm.helpers import instance
from duckterm.helpers.private_files import private_read
from duckterm.mobile.grants import Denied, Grants
from duckterm.mobile.snapshot import snapshot
from duckterm.transport.httpio import parse_request_line, read_body, read_headers, write_response

if TYPE_CHECKING:
    from duckterm.server import Server


class Bridge:
    def __init__(self, server: Server, directory: Path) -> None:
        self.server = server
        self.directory = directory
        self.grants = Grants(directory)
        self.listener: asyncio.Server | None = None
        self.connections: set[asyncio.StreamWriter] = set()
        self.claims: deque[float] = deque()

    async def dispatch(
        self, method: str, target: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, Any]]:
        # Never accept browser-origin requests, owner tokens, session grants,
        # arbitrary forwarding paths, or mutation operations on the desktop.
        if "origin" in headers or "referer" in headers:
            return 403, {"error": "Browser access is unavailable"}
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return 400, {"error": "Invalid request target"}
        if not (self.directory / "ducky-bridge.json").is_file():
            return 403, {"error": "Ducky access is disabled"}
        try:
            if method == "POST" and target == "/v1/pair":
                now = time.monotonic()
                while self.claims and self.claims[0] < now - 60:
                    self.claims.popleft()
                if len(self.claims) >= 20:
                    return 429, {"error": "Too many pairing attempts; try again shortly"}
                self.claims.append(now)
                request = json.loads(body)
                if (
                    not isinstance(request, dict)
                    or set(request) != {"invite", "token", "name"}
                    or not all(isinstance(value, str) for value in request.values())
                ):
                    raise ValueError("Expected invitation, device credential and name")
                result = await asyncio.to_thread(
                    self.grants.claim, request["invite"], request["token"], request["name"]
                )
                return 202, result
            if method != "GET" or parsed.path not in {"/v1/snapshot", "/v1/pair/status"}:
                return 404, {"error": "Read-only endpoint unavailable"}
            authorization = headers.get("authorization", "")
            if not authorization.startswith("Ducky "):
                raise Denied("Ducky device credential required")
            device = await asyncio.to_thread(
                self.grants.authenticate,
                authorization.removeprefix("Ducky "),
                pending=parsed.path == "/v1/pair/status",
            )
            if parsed.path == "/v1/pair/status":
                if parsed.query:
                    raise ValueError("Unexpected query")
                return 200, device
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            if set(query) - {"cursor"} or any(len(values) != 1 for values in query.values()):
                raise ValueError("Invalid snapshot query")
            cursor = query.get("cursor", [None])[0]
            if cursor is not None and (not cursor.isalnum() or len(cursor) > 64):
                raise ValueError("Invalid event cursor")
            return 200, snapshot(self.server, device["host_id"], int(time.time() * 1000), cursor)
        except Denied as exc:
            return 403, {"error": str(exc)}
        except (ValueError, TypeError):
            return 400, {"error": "Invalid Ducky request"}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self.connections) >= 32:
            writer.close()
            return
        self.connections.add(writer)
        try:
            async with asyncio.timeout(5):
                method, target = parse_request_line(await reader.readline())
                if not target.startswith("/") or len(target) > 2048:
                    raise ValueError("Invalid target")
                headers = await read_headers(reader)
                body = await read_body(reader, headers, max_bytes=2048)
                status, response = await self.dispatch(method, target, headers, body)
                await write_response(
                    writer,
                    status,
                    json.dumps(response),
                    {"Cache-Control": "no-store"},
                    "application/json",
                )
        except (TimeoutError, ValueError, asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception:
            # A damaged optional grant store must not expose traceback details or
            # terminate the desktop server's accept loop.
            with contextlib.suppress(ConnectionError, OSError):
                await write_response(
                    writer,
                    500,
                    '{"error":"Ducky access unavailable"}',
                    {"Cache-Control": "no-store"},
                    "application/json",
                )
        finally:
            self.connections.discard(writer)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def close(self) -> None:
        if self.listener is not None:
            self.listener.close()
            await self.listener.wait_closed()
        for writer in tuple(self.connections):
            writer.close()


async def start(server: Server) -> Bridge | None:
    directory = instance.home()
    raw = private_read(directory / "ducky-bridge.json")
    if not raw:
        return None
    config = json.loads(raw)
    if config.get("version") != 1:
        raise ValueError("Unsupported Ducky bridge configuration")
    # No plaintext fallback. The CLI records an explicit private network IP.
    from duckterm.mobile.cli import address

    host = address(config["address"])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(directory / "ducky-cert.pem", directory / "ducky-key.pem")
    bridge = Bridge(server, directory)
    bridge.listener = await asyncio.start_server(
        bridge.handle, host, config["port"], ssl=context, ssl_handshake_timeout=5, limit=8192
    )
    return bridge
