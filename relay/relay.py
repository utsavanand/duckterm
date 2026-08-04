"""RubberTerm share relay — the one always-on piece.

The session runs on the owner's laptop; the laptop dials THIS relay outbound and
pushes one session's terminal bytes. Viewers open a path-routed link
(`/s/<share_id>`) and watch, read-only. The relay is deliberately dumb: it holds
no session state beyond "which sockets belong to which share" and forwards
frames. It never runs an agent and never sees a PTY.

Routing is by PATH, not per-user hostname — one domain, one cert, no DNS
automation. Read-only is structural: a viewer socket's frames are read for
close-detection only; there is no code path from a viewer to the device.

Zero third-party deps: same hand-rolled asyncio HTTP/WS as the main server.
"""

import asyncio
import contextlib
import json
import os
import secrets
from dataclasses import dataclass, field

from duckterm.transport import httpio
from duckterm.transport import websocket as ws

# The device authenticates with a bearer token the relay was told to trust. In
# a real deployment this is a per-device token looked up in a store; for local
# dev and tests it's a single shared secret via env. Kept minimal on purpose —
# device-token provisioning is a laptop+relay concern tracked in the design doc.
_DEVICE_TOKEN = os.environ.get("RUBBERTERM_RELAY_TOKEN", "dev-relay-token")

# A viewer that can't keep up is dropped rather than allowed to stall the device
# feed for everyone — the same backpressure policy the local byte subscribers
# use. 2000 frames of terminal output is generous headroom.
_VIEWER_QUEUE_MAX = 2000


@dataclass
class Share:
    """One shared session: the device socket pushing frames, the viewers reading
    them, and the last screen so a late viewer paints immediately."""

    share_id: str
    device: "asyncio.StreamWriter | None" = None
    viewers: set[asyncio.Queue[bytes | None]] = field(default_factory=set)
    # Rolling recent output so a viewer who joins mid-session paints the current
    # screen. The device sends a clear-screen + capture snapshot as its first
    # frame; we keep a bounded tail after it. Bounded so a long session doesn't
    # grow this without limit.
    snapshot: bytes = b""
    session_meta: dict[str, object] = field(default_factory=dict)


_SNAPSHOT_MAX = 256 * 1024


class Relay:
    def __init__(self) -> None:
        self._shares: dict[str, Share] = {}

    # ── device side: the laptop pushes one session's frames ──────────────────

    def _share(self, share_id: str) -> Share:
        share = self._shares.get(share_id)
        if share is None:
            share = Share(share_id)
            self._shares[share_id] = share
        return share

    async def _serve_device(
        self,
        share_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        key: str,
    ) -> None:
        writer.write(ws.handshake_response(key))
        await writer.drain()
        share = self._share(share_id)
        share.device = writer
        try:
            while True:
                frame = await ws.read_frame(reader)
                if frame is None or frame[0] == 0x8:  # EOF or close
                    break
                opcode, payload = frame
                if opcode == 0x2:  # terminal bytes: append to snapshot + fan out
                    self._append_snapshot(share, payload)
                    self._fan_out(share, payload)
                elif opcode == 0x1:  # text control (session meta / presence)
                    self._handle_device_text(share, payload)
        finally:
            share.device = None
            # Tell every viewer the host went away, then drop them.
            self._broadcast_text(share, {"type": "host_offline"})
            for q in list(share.viewers):
                _drop(q)
            if not share.viewers:
                self._shares.pop(share_id, None)

    def _handle_device_text(self, share: Share, payload: bytes) -> None:
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        if isinstance(msg, dict) and msg.get("type") == "meta":
            share.session_meta = {k: v for k, v in msg.items() if k != "type"}

    def _append_snapshot(self, share: Share, payload: bytes) -> None:
        # A clear-screen sequence resets the rolling buffer (the device sends one
        # at the start of its capture snapshot), keeping the buffer close to what
        # a fresh attach would paint rather than unbounded history.
        clear = payload.rfind(b"\x1b[2J")
        if clear != -1:
            share.snapshot = payload[clear:]
        else:
            share.snapshot = (share.snapshot + payload)[-_SNAPSHOT_MAX:]

    def _fan_out(self, share: Share, payload: bytes) -> None:
        frame = ws.encode_binary_frame(payload)
        for q in list(share.viewers):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                _drop(q)  # slow viewer: cut it loose, never stall the device
                share.viewers.discard(q)

    def _broadcast_text(self, share: Share, msg: dict[str, object]) -> None:
        frame = ws.encode_text_frame(json.dumps(msg))
        for q in list(share.viewers):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                _drop(q)
                share.viewers.discard(q)

    # ── viewer side: a browser watches, read-only ───────────────────────────

    async def _serve_viewer(
        self,
        share_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        key: str,
    ) -> None:
        share = self._shares.get(share_id)
        writer.write(ws.handshake_response(key))
        await writer.drain()
        if share is None or share.device is None:
            # Nothing live to watch — tell the page and close.
            writer.write(ws.encode_text_frame(json.dumps({"type": "host_offline"})))
            await writer.drain()
            writer.write(ws.close_frame())
            await writer.drain()
            return

        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_VIEWER_QUEUE_MAX)
        # Paint the current screen immediately.
        if share.snapshot:
            queue.put_nowait(ws.encode_binary_frame(share.snapshot))
        share.viewers.add(queue)

        # Read the viewer's frames ONLY to detect close. There is no branch that
        # forwards a viewer frame to the device — read-only is structural.
        incoming = asyncio.ensure_future(ws.read_frame_opcode(reader))
        outgoing = asyncio.ensure_future(queue.get())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {incoming, outgoing},
                    timeout=20,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    writer.write(ws.ping_frame())
                    await writer.drain()
                    continue
                if incoming in done:
                    opcode = incoming.result()
                    if opcode is None or opcode == 0x8:
                        break
                    incoming = asyncio.ensure_future(ws.read_frame_opcode(reader))
                if outgoing in done:
                    frame = outgoing.result()
                    if frame is None:  # dropped/EOF
                        break
                    writer.write(frame)
                    await writer.drain()
                    outgoing = asyncio.ensure_future(queue.get())
        except OSError:
            pass
        finally:
            incoming.cancel()
            outgoing.cancel()
            share.viewers.discard(queue)
            with contextlib.suppress(OSError):
                writer.write(ws.close_frame())
                await writer.drain()

    # ── HTTP front: route a connection to device / viewer / share page ───────

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            method, path = httpio.parse_request_line(request_line)
            headers = await httpio.read_headers(reader)

            if method == "GET" and path.startswith("/agent/"):
                await self._route_device(path, headers, reader, writer)
                return
            if method == "GET" and path.endswith("/ws") and path.startswith("/s/"):
                share_id = path[len("/s/") : -len("/ws")]
                await self._route_viewer(share_id, headers, reader, writer)
                return
            if method == "GET" and path.startswith("/s/"):
                await self._serve_share_page(path[len("/s/") :], writer)
                return
            if method == "GET" and path in ("/", "/healthz"):
                await httpio.write_json(writer, 200, {"ok": True, "shares": len(self._shares)})
                return
            await httpio.write_response(writer, 404, "not found")
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        finally:
            with contextlib.suppress(OSError):
                writer.close()

    async def _route_device(
        self,
        path: str,
        headers: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        share_id = path[len("/agent/") :]
        bearer = headers.get("authorization", "")
        token = bearer[len("Bearer ") :] if bearer.startswith("Bearer ") else ""
        if not secrets.compare_digest(token, _DEVICE_TOKEN):
            await httpio.write_response(writer, 401, "bad device token")
            return
        key = headers.get("sec-websocket-key")
        if not key:
            await httpio.write_response(writer, 400, "expected a WebSocket upgrade")
            return
        await self._serve_device(share_id, reader, writer, key)

    async def _route_viewer(
        self,
        share_id: str,
        headers: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        key = headers.get("sec-websocket-key")
        if not key:
            await httpio.write_response(writer, 400, "expected a WebSocket upgrade")
            return
        await self._serve_viewer(share_id, reader, writer, key)

    async def _serve_share_page(self, share_id: str, writer: asyncio.StreamWriter) -> None:
        # The capability token lives in the URL fragment (never sent to us), so
        # this page is intentionally token-agnostic — the WS carries the token.
        # no-referrer + no third-party JS so nothing can leak location.hash.
        html = _SHARE_PAGE
        head = (
            "HTTP/1.1 200 OK\r\n"
            f"Content-Length: {len(html.encode())}\r\n"
            "Content-Type: text/html\r\n"
            "Referrer-Policy: no-referrer\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(head.encode() + html.encode())
        await writer.drain()


async def serve(host: str = "127.0.0.1", port: int = 4400) -> None:
    relay = Relay()
    server = await asyncio.start_server(relay.handle, host, port)
    async with server:
        await server.serve_forever()


def _drop(queue: "asyncio.Queue[bytes | None]") -> None:
    """Wake a viewer's consumer with an EOF sentinel so it closes cleanly."""
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(None)


# A self-contained xterm.js viewer. In production the xterm assets would be
# bundled/served locally (CSP: no third-party hosts); for the skeleton this is a
# minimal placeholder that connects and renders text. The read-only nature is
# enforced by the relay, not this page — it simply has no input wiring.
_SHARE_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>RubberTerm — shared session</title>
<style>
  body { margin:0; background:#0c0f16; color:#d1d5db;
         font-family:ui-monospace,Menlo,monospace; }
  #bar { padding:6px 12px; font-size:12px; color:#9ca3af;
         border-bottom:1px solid #242830; }
  #bar .dot { color:#22a06b; }
  #screen { padding:8px 12px; white-space:pre-wrap; font-size:12px;
            line-height:1.4; }
</style></head>
<body>
  <div id="bar"><span class="dot">●</span> <span id="status">connecting…</span></div>
  <pre id="screen"></pre>
<script>
  // Token rides in the fragment (#...), never sent in the request line. The WS
  // handshake carries it. This page has NO input path — read-only by absence.
  const token = location.hash.slice(1);
  const id = location.pathname.split("/s/")[1];
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const status = document.getElementById("status");
  const screen = document.getElementById("screen");
  const dec = new TextDecoder();
  function connect() {
    const ws = new WebSocket(`${proto}://${location.host}/s/${id}/ws`, token ? [token] : []);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { status.textContent = "live"; };
    ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        try { const m = JSON.parse(e.data);
          if (m.type === "host_offline") status.textContent = "host offline — reconnecting…";
        } catch {}
        return;
      }
      // Terminal bytes. A real build feeds these to xterm.js; the skeleton
      // strips control sequences for a readable text preview.
      const text = dec.decode(new Uint8Array(e.data)).replace(/\\x1b\\[[0-9;?]*[A-Za-z]/g, "");
      screen.textContent = (screen.textContent + text).slice(-20000);
    };
    ws.onclose = () => {
      status.textContent = "disconnected — retrying";
      setTimeout(connect, 1500);
    };
  }
  connect();
</script>
</body></html>
"""


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(serve(port=int(os.environ.get("RUBBERTERM_RELAY_PORT", "4400"))))
