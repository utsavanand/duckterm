"""Disposable loopback TLS hosts for native tests; no real configuration or credentials."""

import asyncio
import json
import os
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

from duckterm.editions import LITE
from duckterm.mobile.bridge import Bridge
from duckterm.mobile.cli import configure, invitation
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


async def run(root: Path, mode: str) -> None:
    os.environ["HOME"] = str(root)
    os.environ["DUCKTERM_HOME"] = str(root)
    os.environ["DUCKTERM_TMUX_SOCKET"] = "ducky-native-test-" + str(os.getpid())
    os.environ["DUCKTERM_SUMMARIZER"] = "off"
    configure(
        root, "127.0.0.1", 4430, hostname="ducky-native-fixture.local" if mode == "local" else None
    )
    path = root / "ducky-bridge.json"
    config = json.loads(path.read_text())
    if mode == "legacy-local":
        # Reuse an existing IP-only leaf while newly pairing with a stable name.
        config.update(hostname="ducky-native-fixture.local", tls_name="127.0.0.1")
    history = HistoryStore(root / "state.sqlite", edition=LITE)
    server = Server(history=history, edition=LITE)
    bridge = Bridge(server, root)
    secondary = None
    counters = {"requests": 0, "handshakes": 0}
    redirect_next = False
    original_dispatch = bridge.dispatch

    async def dispatch(*args):
        counters["requests"] += 1
        return await original_dispatch(*args)

    bridge.dispatch = dispatch

    async def handle(reader, writer):
        nonlocal redirect_next
        if not redirect_next:
            await bridge.handle(reader, writer)
            return
        redirect_next = False
        try:
            async with asyncio.timeout(3):
                await reader.readuntil(b"\r\n\r\n")
                counters["requests"] += 1
                # A loopback-only redirect detects following without risking external traffic.
                writer.write(
                    f"HTTP/1.1 302 Found\r\nLocation: https://[::1]:{config['port']}/v1/pair/status\r\n"
                    "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(root / "ducky-cert.pem", root / "ducky-key.pem")

    def handshake(*_args):
        counters["handshakes"] += 1

    context.set_servername_callback(handshake)
    try:
        history.record(
            {
                "_id": "initial",
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": "test-agent",
                "test": True,
            }
        )
        history.set_meta("test-agent", name="Native protocol test", group="Test folder")
        bridge.listener = await asyncio.start_server(
            handle, "127.0.0.1", 0, ssl=context, ssl_handshake_timeout=3, limit=8192
        )
        config["port"] = bridge.listener.sockets[0].getsockname()[1]
        # One identity on two distinct private literals models DHCP/address-family churn.
        secondary = await asyncio.start_server(
            handle, "::1", config["port"], ssl=context, ssl_handshake_timeout=3, limit=8192
        )
        path.write_text(json.dumps(config))
        certificate = subprocess.run(
            ["openssl", "x509", "-in", str(root / "ducky-cert.pem"), "-noout", "-text"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        assert "CA:FALSE" in certificate and "CA:TRUE" not in certificate
        print(invitation(root), flush=True)
        while line := await asyncio.to_thread(sys.stdin.readline):
            operation, identifier = line.strip().split(" ", 1)
            if operation == "approve":
                bridge.grants.approve(identifier)
            elif operation == "revoke":
                bridge.grants.revoke(identifier)
            elif operation == "redirect":
                redirect_next = True
            elif operation == "stop-v4":
                bridge.listener.close()
                await bridge.listener.wait_closed()
                bridge.listener = None
            elif operation == "stats":
                print(json.dumps({**counters, "ca": False}), flush=True)
                continue
            else:
                raise ValueError("Unknown fixture action")
            print("ok", flush=True)
    finally:
        if secondary is not None:
            secondary.close()
            await secondary.wait_closed()
        await bridge.close()
        history.purge_test_sessions()
        history.close()


if __name__ == "__main__":
    fixture_mode = sys.argv[1] if len(sys.argv) > 1 else "literal"
    if fixture_mode not in {"literal", "local", "legacy-local"}:
        raise ValueError("Unknown fixture mode")
    with tempfile.TemporaryDirectory(prefix="ducky-native-test-") as directory:
        asyncio.run(run(Path(directory), fixture_mode))
