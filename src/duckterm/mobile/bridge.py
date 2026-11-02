"""An opt-in TLS listener with its own credentials and explicitly scoped conversation APIs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import ssl
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from duckterm.helpers import instance
from duckterm.helpers.private_files import private_read
from duckterm.mobile.conversation import (
    ConversationNotFound,
    ConversationUnavailable,
    conversation_available,
    read_conversation,
)
from duckterm.mobile.grants import Denied, Grants
from duckterm.mobile.message_store import MessageError
from duckterm.mobile.messages import Messages
from duckterm.mobile.snapshot import snapshot
from duckterm.transport.httpio import parse_request_line, read_body, read_headers, write_response

if TYPE_CHECKING:
    from duckterm.server import Server


class Bridge:
    def __init__(self, server: Server, directory: Path, messages: Messages | None = None) -> None:
        self.server = server
        self.directory = directory
        self.grants = Grants(directory)
        self.messages = messages or Messages(server, directory)
        self.owns_messages = messages is None
        self.listener: asyncio.Server | None = None
        self.connections: set[asyncio.StreamWriter] = set()
        self.claims: deque[float] = deque()
        self.initial_cursor = secrets.token_hex(16)

    async def dispatch(
        self, method: str, target: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, Any]]:
        # Never accept browser-origin requests, owner tokens, session grants,
        # arbitrary forwarding paths, or unscoped desktop operations.
        if "origin" in headers or "referer" in headers:
            return 403, {"error": "Browser access is unavailable"}
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return 400, {"error": "Invalid request target"}
        if not (self.directory / "ducky-bridge.json").is_file():
            return 503, {"error": "Ducky access is disabled"}
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
            reads = {
                "/v1/snapshot",
                "/v1/pair/status",
                "/v2/snapshot",
                "/v2/conversation",
                "/v2/messages",
            }
            if not (
                (method == "GET" and parsed.path in reads)
                or (method == "POST" and target == "/v2/messages")
            ):
                return 404, {"error": "Endpoint unavailable"}
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
            if any(len(values) != 1 for values in query.values()):
                raise ValueError("Duplicate query")
            if parsed.path == "/v2/messages":
                if "session:send" not in device["capabilities"]:
                    raise Denied("Mac conversation permission required")
                if method == "POST":
                    if len(body) > 32768:
                        raise MessageError(413, "message_too_large")
                    result = await self.messages.accept(device, json.loads(body))
                    status = 202
                else:
                    if set(query) != {"request_id"} or len(query["request_id"][0]) > 64:
                        raise ValueError("Invalid receipt query")
                    result = await self.messages.lookup(device, query["request_id"][0])
                    status = 200
                await self.recheck(device, "session:send")
                return status, {"version": 1, "host_id": device["host_id"], "receipt": result}
            if parsed.path == "/v2/conversation":
                if "conversation:read" not in device["capabilities"]:
                    raise Denied("Mac conversation permission required")
                if set(query) != {"session_id"} or not 0 < len(query["session_id"][0]) <= 256:
                    raise ValueError("Invalid conversation query")
                key = query["session_id"][0]
                row = self.server.history.session(key)
                if row is None or row["state"] in {"archived", "merged"}:
                    raise ConversationNotFound()
                result = await read_conversation(self.server, key)
                receipts = await self.messages.receipts(device, key)
                await self.recheck(device, "conversation:read")
                return 200, {
                    "version": 1,
                    "host_id": device["host_id"],
                    "session_id": key,
                    **result,
                    "receipts": receipts,
                    **self.messages.fields(key, device),
                }
            if set(query) - {"cursor"}:
                raise ValueError("Invalid snapshot query")
            cursor = query.get("cursor", [None])[0]
            if cursor is not None and (not cursor.isalnum() or len(cursor) > 64):
                raise ValueError("Invalid event cursor")
            expanded = parsed.path == "/v2/snapshot"
            result = snapshot(
                self.server,
                device["host_id"],
                int(time.time() * 1000),
                cursor,
                initial_cursor=self.initial_cursor,
                include_inactive=expanded,
            )
            if expanded:
                result.update(
                    version=2, features=["conversations"], capabilities=device["capabilities"]
                )
                for session in result["sessions"]:
                    session.update(self.messages.fields(session["id"], device))
                    session["can_read"] = "conversation:read" in device[
                        "capabilities"
                    ] and conversation_available(self.server, session["id"])
            return 200, result
        except ConversationNotFound as exc:
            return 404, {"error": exc.reason}
        except ConversationUnavailable as exc:
            return 503, {"error": exc.reason}
        except MessageError as exc:
            return exc.status, {"error": exc.reason}
        except TimeoutError:
            return 503, {"error": "Ducky access temporarily unavailable"}
        except Denied as exc:
            return 403, {"error": str(exc)}
        except (ValueError, TypeError):
            return 400, {"error": "Invalid Ducky request"}

    async def recheck(self, device: dict[str, Any], required: str) -> None:
        await asyncio.to_thread(
            self.grants.authenticated_by_id, device["id"], required, host_id=device["host_id"]
        )
        if not (self.directory / "ducky-bridge.json").is_file():
            raise MessageError(503, "disabled")

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
                limit = 32768 if method == "POST" and target == "/v2/messages" else 2048
                if int(headers.get("content-length", "0")) > limit:
                    await write_response(
                        writer,
                        413,
                        '{"error":"Request too large"}',
                        {"Cache-Control": "no-store"},
                        "application/json",
                    )
                    return
                body = await read_body(reader, headers, max_bytes=limit)
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
        if self.owns_messages:
            await self.messages.close()
        if self.listener is not None:
            self.listener.close()
            await self.listener.wait_closed()
        for writer in tuple(self.connections):
            writer.close()


async def start(
    server: Server,
    *,
    directory: Path | None = None,
    bind_address: str | None = None,
    messages: Messages | None = None,
) -> Bridge | None:
    directory = directory or instance.home()
    raw = private_read(directory / "ducky-bridge.json")
    if not raw:
        return None
    config = json.loads(raw)
    if config.get("version") != 1:
        raise ValueError("Unsupported Ducky bridge configuration")
    # No plaintext fallback. The CLI records an explicit private network IP.
    from duckterm.mobile.cli import address

    host = address(bind_address if bind_address is not None else config["address"])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(directory / "ducky-cert.pem", directory / "ducky-key.pem")
    bridge = Bridge(server, directory, messages)
    bridge.listener = await asyncio.start_server(
        bridge.handle, host, config["port"], ssl=context, ssl_handshake_timeout=5, limit=8192
    )
    return bridge
