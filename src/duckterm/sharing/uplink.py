"""The laptop side of session sharing: dial the relay OUTBOUND and push one
session's terminal bytes.

Outbound works through every NAT/corporate firewall — it's ordinary HTTPS. The
uplink consumes the same `subscribe_bytes()` feed the local browser terminal
uses, so a viewer sees exactly what the owner sees, with the same clear-screen
snapshot on attach. There is NO inbound path here — the relay never sends the
laptop anything that reaches the PTY; read-only is structural on both ends.

Reconnects with jittered backoff so a relay redeploy doesn't cause every
laptop to reconnect in lockstep.
"""

import asyncio
import contextlib
import json
import urllib.parse
from dataclasses import dataclass

from duckterm.transport import wsclient


@dataclass
class RelayTarget:
    """Where and how to reach the relay for one share."""

    url: str  # e.g. wss://share.rubberterm.com
    share_id: str
    device_token: str


def _split(url: str) -> tuple[str, str, int, bool]:
    """(host_header, host, port, tls) from a ws(s):// or http(s):// URL."""
    u = urllib.parse.urlparse(url)
    tls = u.scheme in ("wss", "https")
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if tls else 80)
    host_header = host if u.port is None else f"{host}:{u.port}"
    return host_header, host, port, tls


class ShareUplink:
    """Pushes a supervisor's byte feed to the relay until stopped. One per
    active share. `subscribe` is any object exposing `subscribe_bytes()` and,
    optionally, a meta dict — i.e. a SessionSupervisor."""

    def __init__(self, target: RelayTarget, supervisor: object, meta: dict[str, object]):
        self._target = target
        self._supervisor = supervisor
        self._meta = meta
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        attempts = 0
        while not self._stopped.is_set():
            try:
                await self._connect_once()
                attempts = 0
            except (OSError, asyncio.IncompleteReadError):
                pass
            if self._stopped.is_set():
                break
            attempts += 1
            # Jittered exponential backoff: base 1.5s, cap 15s. Jitter (via the
            # low bits of the attempt count fed through a fixed sequence) keeps a
            # relay redeploy from triggering a synchronized reconnect storm; we
            # avoid Math.random-style nondeterminism by deriving jitter from the
            # attempt number so tests stay reproducible.
            base = min(1.5 * 1.5 ** min(attempts - 1, 6), 15.0)
            jitter = 0.5 + ((attempts * 2654435761) % 1000) / 1000.0  # 0.5–1.5×
            await asyncio.sleep(base * jitter)

    async def _connect_once(self) -> None:
        host_header, host, port, tls = _split(self._target.url)
        ssl = tls or None
        reader, writer = await asyncio.open_connection(host, port, ssl=ssl)
        try:
            handshake, key = wsclient.client_handshake(
                host_header,
                f"/agent/{self._target.share_id}",
                {"Authorization": f"Bearer {self._target.device_token}"},
            )
            writer.write(handshake)
            await writer.drain()
            resp = await _read_http_response_head(reader)
            if "101" not in resp.split("\r\n", 1)[0] or not wsclient.accept_ok(resp, key):
                raise OSError(f"relay refused share {self._target.share_id}: {resp!r}")

            # Announce session metadata (name/state) so the viewer page can label
            # the share, then stream the byte feed.
            writer.write(wsclient.mask_text(json.dumps({"type": "meta", **self._meta})))
            await writer.drain()
            await self._pump(reader, writer)
        finally:
            with contextlib.suppress(OSError):
                writer.write(wsclient.mask_close())
                await writer.drain()
                writer.close()

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        feed = self._supervisor.subscribe_bytes()  # type: ignore[attr-defined]
        outgoing = asyncio.ensure_future(feed.__anext__())
        # Read the relay's frames only to notice a close (it never sends input).
        incoming = asyncio.ensure_future(wsclient.read_server_frame(reader))
        try:
            while not self._stopped.is_set():
                done, _ = await asyncio.wait(
                    {outgoing, incoming}, timeout=20, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:  # keepalive
                    writer.write(wsclient.encode_masked_frame(0x9, b""))
                    await writer.drain()
                    continue
                if incoming in done:
                    frame = incoming.result()
                    if frame is None or frame[0] == 0x8:
                        break
                    if frame[0] == 0x9:  # ping -> pong
                        writer.write(wsclient.mask_pong())
                        await writer.drain()
                    incoming = asyncio.ensure_future(wsclient.read_server_frame(reader))
                if outgoing in done:
                    writer.write(wsclient.mask_binary(outgoing.result()))
                    await writer.drain()
                    outgoing = asyncio.ensure_future(feed.__anext__())
        except StopAsyncIteration:
            pass
        finally:
            outgoing.cancel()
            incoming.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await outgoing
            await feed.aclose()


async def _read_http_response_head(reader: asyncio.StreamReader) -> str:
    """Read up to the blank line separating the HTTP response head from any
    upgraded stream."""
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = await reader.readline()
        if not chunk:
            break
        head += chunk
    return head.decode("latin-1")
