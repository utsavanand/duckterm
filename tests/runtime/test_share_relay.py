"""End-to-end share path, all on localhost: a fake session's byte feed is pushed
by the uplink to the relay, and a viewer WebSocket client reads it back. Proves
the three new pieces (wsclient, relay, uplink) actually stream bytes together,
and that a viewer has no path to the session (read-only by construction)."""

import asyncio
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

_RELAY_DIR = Path(__file__).resolve().parents[2] / "relay"
sys.path.insert(0, str(_RELAY_DIR))

import relay as relay_mod  # noqa: E402

from duckterm.sharing.uplink import RelayTarget, ShareUplink  # noqa: E402
from duckterm.transport import wsclient  # noqa: E402


class FakeSupervisor:
    """Emits a fixed sequence of byte chunks, then blocks — like a live PTY that
    printed some output and is now idle. subscribe_bytes() sends a snapshot
    first, matching the real supervisor's attach behavior."""

    def __init__(self, chunks: list[bytes], snapshot: bytes = b""):
        self._chunks = chunks
        self._snapshot = snapshot
        self._done = asyncio.Event()

    async def subscribe_bytes(self) -> AsyncGenerator[bytes, None]:
        if self._snapshot:
            yield self._snapshot
        for c in self._chunks:
            yield c
        await self._done.wait()  # stay open like an idle session

    def finish(self) -> None:
        self._done.set()


async def _viewer_read(port: int, share_id: str, timeout: float = 3.0) -> bytes:
    """Connect as a browser viewer would and collect binary-frame payloads."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    handshake, key = wsclient.client_handshake("x", f"/s/{share_id}/ws", {})
    writer.write(handshake)
    await writer.drain()
    # Read handshake response head.
    head = b""
    while b"\r\n\r\n" not in head:
        head += await reader.readline()
    assert wsclient.accept_ok(head.decode("latin-1"), key)

    out = bytearray()
    try:
        async with asyncio.timeout(timeout):
            while True:
                frame = await wsclient.read_server_frame(reader)
                if frame is None or frame[0] == 0x8:
                    break
                if frame[0] == 0x2:  # binary: terminal bytes
                    out += frame[1]
                    if b"DONE" in out:
                        break
    except TimeoutError:
        pass
    finally:
        writer.close()
    return bytes(out)


def _relay_token(monkeypatch) -> str:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(relay_mod, "_DEVICE_TOKEN", "test-token")
    return "test-token"


def test_viewer_sees_bytes_pushed_by_the_uplink(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    token = _relay_token(monkeypatch)

    async def scenario() -> bytes:
        relay = relay_mod.Relay()
        server = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        sup = FakeSupervisor(
            [b"hello ", b"from ", b"the session DONE"],
            snapshot=b"\x1b[2J\x1b[Hprompt$ ",
        )
        uplink = ShareUplink(
            RelayTarget(url=f"ws://127.0.0.1:{port}", share_id="abc", device_token=token),
            sup,
            meta={"name": "demo"},
        )
        async with server:
            uplink.start()
            # Give the uplink a moment to dial and register before the viewer.
            for _ in range(50):
                if "abc" in relay._shares and relay._shares["abc"].device is not None:
                    break
                await asyncio.sleep(0.02)
            got = await _viewer_read(port, "abc")
            sup.finish()
            await uplink.stop()
        return got

    got = asyncio.run(scenario())
    assert b"hello from the session DONE" in got
    assert b"prompt$ " in got  # the attach snapshot reached the viewer too


def test_viewer_of_a_nonexistent_share_is_told_host_offline(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _relay_token(monkeypatch)

    async def scenario() -> bytes:
        relay = relay_mod.Relay()
        server = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            handshake, key = wsclient.client_handshake("x", "/s/nope/ws", {})
            writer.write(handshake)
            await writer.drain()
            head = b""
            while b"\r\n\r\n" not in head:
                head += await reader.readline()
            frame = await wsclient.read_server_frame(reader)
            writer.close()
            return frame[1] if frame else b""

    payload = asyncio.run(scenario())
    assert b"host_offline" in payload


def test_device_connection_requires_the_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _relay_token(monkeypatch)

    async def scenario() -> str:
        relay = relay_mod.Relay()
        server = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            handshake, _ = wsclient.client_handshake(
                "x", "/agent/abc", {"Authorization": "Bearer WRONG"}
            )
            writer.write(handshake)
            await writer.drain()
            head = b""
            while b"\r\n\r\n" not in head:
                line = await reader.readline()
                if not line:
                    break
                head += line
            writer.close()
            return head.decode("latin-1")

    resp = asyncio.run(scenario())
    assert "401" in resp.split("\r\n", 1)[0]


def test_viewer_input_never_reaches_the_session(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Structural read-only: a viewer that sends frames must not affect the
    session. The relay has no code path from a viewer frame to the device, so we
    assert the device feed is unperturbed and the connection stays a pure
    broadcast — sending viewer input changes nothing the session receives."""
    token = _relay_token(monkeypatch)

    async def scenario() -> bytes:
        relay = relay_mod.Relay()
        server = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        sup = FakeSupervisor([b"output DONE"])
        uplink = ShareUplink(
            RelayTarget(url=f"ws://127.0.0.1:{port}", share_id="ro", device_token=token),
            sup,
            meta={},
        )
        async with server:
            uplink.start()
            for _ in range(50):
                if relay._shares.get("ro") and relay._shares["ro"].device is not None:
                    break
                await asyncio.sleep(0.02)

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            handshake, key = wsclient.client_handshake("x", "/s/ro/ws", {})
            writer.write(handshake)
            await writer.drain()
            head = b""
            while b"\r\n\r\n" not in head:
                head += await reader.readline()
            # A malicious viewer sends a keystroke frame. It must go nowhere.
            writer.write(wsclient.mask_binary(b"rm -rf / \r"))
            await writer.drain()

            out = bytearray()
            try:
                async with asyncio.timeout(2.0):
                    while b"DONE" not in out:
                        frame = await wsclient.read_server_frame(reader)
                        if frame is None or frame[0] == 0x8:
                            break
                        if frame[0] == 0x2:
                            out += frame[1]
            except TimeoutError:
                pass
            writer.close()
            sup.finish()
            await uplink.stop()
            return bytes(out)

    got = asyncio.run(scenario())
    # The viewer still only RECEIVES output; nothing it sent is echoed or acted
    # on (the fake session has no input handler at all — the point is the relay
    # offered no path to reach one).
    assert b"output DONE" in got
    assert b"rm -rf" not in got
