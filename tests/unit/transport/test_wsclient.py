"""The client half of the WebSocket transport: masked frames round-trip through
the server-side reader, and the handshake accept check works."""

import asyncio

from duckterm.transport import websocket as ws
from duckterm.transport import wsclient


def test_masked_client_frame_is_read_correctly_by_the_server_reader() -> None:
    """A client frame this module masks must decode to the original payload via
    the server's read_frame (which unmasks). This is the exact laptop→relay
    path."""

    async def scenario() -> tuple[int, bytes]:
        payload = b"keystrokes and \x00 raw bytes \xff"
        frame = wsclient.mask_binary(payload)
        reader = asyncio.StreamReader()
        reader.feed_data(frame)
        reader.feed_eof()
        result = await ws.read_frame(reader)
        assert result is not None
        return result

    opcode, payload = asyncio.run(scenario())
    assert opcode == 0x2
    assert payload == b"keystrokes and \x00 raw bytes \xff"


def test_server_frame_reader_round_trips_unmasked_frames() -> None:
    """The relay's server->client frames are unmasked; read_server_frame must
    decode them (this is the laptop reading the relay's pings/close)."""

    async def scenario() -> tuple[int, bytes]:
        frame = ws.encode_text_frame("hello")
        reader = asyncio.StreamReader()
        reader.feed_data(frame)
        reader.feed_eof()
        result = await wsclient.read_server_frame(reader)
        assert result is not None
        return result

    opcode, payload = asyncio.run(scenario())
    assert opcode == 0x1
    assert payload == b"hello"


def test_accept_ok_matches_the_servers_computed_accept() -> None:
    handshake, key = wsclient.client_handshake("host", "/agent/x", {})
    # The server computes its accept from the key it received.
    server_accept = ws.accept_key(key)
    response = f"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: {server_accept}\r\n\r\n"
    assert wsclient.accept_ok(response, key)
    assert not wsclient.accept_ok("HTTP/1.1 101\r\nSec-WebSocket-Accept: wrong\r\n\r\n", key)


def test_large_payload_uses_extended_length_and_round_trips() -> None:
    """A payload over 125 bytes exercises the 16-bit length path; over 65535 the
    64-bit path. Both must survive the mask+unmask round trip."""

    async def rt(n: int) -> bytes:
        frame = wsclient.mask_binary(b"x" * n)
        reader = asyncio.StreamReader()
        reader.feed_data(frame)
        reader.feed_eof()
        result = await ws.read_frame(reader)
        assert result is not None
        return result[1]

    assert asyncio.run(rt(200)) == b"x" * 200
    assert asyncio.run(rt(70000)) == b"x" * 70000
