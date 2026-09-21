"""HTTP/1.1 wire primitives for the server: parse a request line and headers,
read a body, and write text/JSON/SSE/file responses over an asyncio stream.

Split out of server.py so the routing and handler logic isn't interleaved with
byte-level framing. Stateless functions — no dependency on the Server instance.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from duckterm.core.eventbus import Event

SELF_PROBE_HEADER = "X-Duckterm"
KEEPALIVE_SECONDS = 15
MAX_HEADER_BYTES = 32 * 1024
MAX_REQUEST_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10

_REASON = {
    200: "OK",
    202: "Accepted",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    413: "Payload Too Large",
    429: "Too Many Requests",
    500: "Internal Server Error",
}

_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".ico": "image/x-icon",
}


def parse_request_line(line: bytes) -> tuple[str, str]:
    """Return (method, path) from a request line, or ('', '') if malformed."""
    parts = line.decode("latin-1").split()
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[1]


async def read_headers(reader: asyncio.StreamReader) -> dict[str, str]:
    """Read headers up to the blank line. Names are lower-cased for lookup."""
    headers: dict[str, str] = {}
    total = 0
    while True:
        line = await reader.readline()
        total += len(line)
        if total > MAX_HEADER_BYTES:
            raise ValueError("request headers too large")
        if line in (b"\r\n", b"\n", b""):
            break
        name, separator, value = line.decode("latin-1").partition(":")
        name = name.lower()
        if not separator or not name or name.strip() != name:
            raise ValueError("invalid request header")
        if name in headers:
            raise ValueError("duplicate request header")
        headers[name] = value.strip()
    return headers


async def read_body(reader: asyncio.StreamReader, headers: dict[str, str]) -> bytes:
    """Read exactly Content-Length bytes (empty when absent or zero)."""
    if "transfer-encoding" in headers:
        raise ValueError("Transfer-Encoding is not supported")
    raw = headers.get("content-length", "0")
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError("invalid Content-Length")
    length = int(raw)
    if length > MAX_REQUEST_BYTES:
        raise ValueError("request body too large")
    return await reader.readexactly(length) if length else b""


def sse_frame(event: Event) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


def write_sse(writer: asyncio.StreamWriter, event: Event) -> None:
    writer.write(sse_frame(event))


async def write_response(
    writer: asyncio.StreamWriter,
    status: int,
    text: str,
    extra_headers: dict[str, str] | None = None,
    content_type: str = "text/plain",
) -> None:
    body = text.encode()
    head = f"HTTP/1.1 {status} {_REASON.get(status, 'OK')}\r\n"
    head += f"Content-Length: {len(body)}\r\nContent-Type: {content_type}\r\n"
    for name, value in (extra_headers or {}).items():
        head += f"{name}: {value}\r\n"
    head += "Connection: close\r\n\r\n"
    writer.write(head.encode() + body)
    await writer.drain()


async def write_json(writer: asyncio.StreamWriter, status: int, payload: Any) -> None:
    body = json.dumps(payload).encode()
    head = (
        f"HTTP/1.1 {status} {_REASON.get(status, 'OK')}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Content-Type: application/json\r\n"
        "Connection: close\r\n\r\n"
    )
    writer.write(head.encode() + body)
    await writer.drain()


async def write_file(writer: asyncio.StreamWriter, path: Path) -> None:
    body = path.read_bytes()
    ctype = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
    # index.html must always be revalidated, else browsers serve a stale HTML
    # that points at an old bundle hash and never picks up new builds. The
    # content-hashed assets under /assets/ are immutable — cache them hard.
    cache = "no-cache" if path.suffix == ".html" else "public, max-age=31536000, immutable"
    head = (
        f"HTTP/1.1 200 OK\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Content-Type: {ctype}\r\n"
        f"Cache-Control: {cache}\r\n"
        f"{SELF_PROBE_HEADER}: 1\r\n"
        "Connection: close\r\n\r\n"
    )
    writer.write(head.encode() + body)
    await writer.drain()


def dashboard_dir() -> Path | None:
    """Locate the built dashboard. In a repo checkout, web/dist wins: the
    packaged copy (src/duckterm/dashboard) is a build artifact frozen at the
    last release build, and preferring it silently served a STALE UI to dev
    servers and the e2e suite. An installed package has no web/ tree, so it
    serves its bundled copy.

    This file lives at src/duckterm/transport/httpio.py, so the package root
    is two parents up and the repo root is four."""
    pkg_root = Path(__file__).resolve().parents[1]  # src/duckterm/
    dev = pkg_root.parents[1] / "web" / "dist"  # repo/web/dist
    if (dev / "index.html").is_file():
        return dev
    packaged = pkg_root / "dashboard"
    return packaged if (packaged / "index.html").is_file() else None
