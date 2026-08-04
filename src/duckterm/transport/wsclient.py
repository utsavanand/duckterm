"""The client half of the hand-rolled WebSocket: an OUTBOUND handshake and
client-masked frames. The server half (accept, unmasked server->client frames)
lives in websocket.py.

The laptop's share uplink is a WebSocket *client* — it dials the relay and
pushes terminal bytes. RFC 6455 §5.3 requires client->server frames to be
masked, which websocket.py never needed to emit. Kept in the same zero-dep,
single-frame style; if we ever need fragmentation/deflate, swap in a real
library rather than growing this.
"""

import asyncio
import base64
import os
import struct

from duckterm.transport.websocket import accept_key


def client_handshake(host: str, path: str, headers: dict[str, str]) -> tuple[bytes, str]:
    """The bytes to send to open a client WebSocket, and the Sec-WebSocket-Key
    whose accept value the server must echo back (verified by `accept_ok`)."""
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode(), key


def accept_ok(response_headers: str, sent_key: str) -> bool:
    """True if the server's 101 response accepts our key (proves it spoke the
    WebSocket handshake, not some other 101)."""
    want = accept_key(sent_key).lower()
    for line in response_headers.split("\r\n"):
        if line.lower().startswith("sec-websocket-accept:"):
            return line.split(":", 1)[1].strip().lower() == want
    return False


def encode_masked_frame(opcode: int, payload: bytes) -> bytes:
    """A single, unfragmented, MASKED frame (client->server), as RFC 6455
    requires of clients."""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    return bytes(header) + mask + masked


def mask_binary(payload: bytes) -> bytes:
    return encode_masked_frame(0x2, payload)


def mask_text(text: str) -> bytes:
    return encode_masked_frame(0x1, text.encode())


def mask_close() -> bytes:
    return encode_masked_frame(0x8, b"")


def mask_pong() -> bytes:
    return encode_masked_frame(0xA, b"")


async def read_server_frame(reader: asyncio.StreamReader) -> tuple[int, bytes] | None:
    """Read one server->client frame (unmasked) as (opcode, payload), or None on
    EOF. The relay never masks its frames (it's the server), so no unmasking."""
    try:
        first = await reader.readexactly(1)
        second = await reader.readexactly(1)
    except asyncio.IncompleteReadError:
        return None
    opcode = first[0] & 0x0F
    length = second[0] & 0x7F
    masked = bool(second[0] & 0x80)
    if length == 126:
        length = struct.unpack(">H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else b"\x00\x00\x00\x00"
    payload = bytearray(await reader.readexactly(length)) if length else bytearray()
    if masked:
        for i in range(length):
            payload[i] ^= mask[i & 3]
    return opcode, bytes(payload)
