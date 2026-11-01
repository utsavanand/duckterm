"""Disposable real TLS host for the native viewer's simulator UI test.

Prints a loopback-only control URL. Set that URL as DUCKY_UI_CONTROL in the
XCTest runner, then GET /quit to remove all fixture data and close both listeners.
The control routes exist only in this test process, never in the desktop server.
"""

import asyncio
import json
import os
import signal
import tempfile
import time
from pathlib import Path

from duckterm.editions import LITE
from duckterm.helpers.private_files import private_write
from duckterm.mobile.bridge import start
from duckterm.mobile.cli import configure, invitation
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


async def run(root: Path) -> None:
    os.environ["DUCKTERM_HOME"] = str(root)
    configure(root, "127.0.0.1", 4430)
    config_file = root / "ducky-bridge.json"
    config = json.loads(config_file.read_text())
    config["port"] = 0
    private_write(config_file, json.dumps(config))
    history = HistoryStore(root / "state.sqlite", edition=LITE)
    server = Server(history=history, edition=LITE)
    rows = [
        ("discounts", "Checkout", "claude-code", "busy"),
        ("payments-review", "Checkout", "claude-code", "waiting"),
        ("landing-copy", "Website", "codex", "idle"),
        ("docs-sync", "Website", "claude-code", "busy"),
        ("release-checks", "Infra", "codex", "idle"),
    ]
    now = int(time.time() * 1000)
    for index, (name, folder, runtime, state) in enumerate(rows):
        history.record(
            {
                "_id": "start" + str(index),
                "_ts": now - index * 60000,
                "event_type": "SessionStart",
                "session_key": name,
                "runtime": runtime,
                "test": True,
            }
        )
        history.set_meta(name, name=name, group=folder)
        history.set_state(name, state, now=now - index * 60000)
    bridge = await start(server)
    assert bridge is not None
    config["port"] = bridge.listener.sockets[0].getsockname()[1]
    private_write(config_file, json.dumps(config))
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)

    async def control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal bridge
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            path = request.split(b" ")[1].decode("ascii")
            result = "ok"
            if path == "/invitation":
                result = invitation(root)
            elif path == "/approve":
                for device in bridge.grants.devices():
                    if device["status"] == "pending":
                        bridge.grants.approve(device["id"])
            elif path == "/revoke":
                for device in bridge.grants.devices():
                    bridge.grants.revoke(device["id"])
            elif path == "/offline":
                await bridge.close()
            elif path == "/online":
                bridge = await start(server)
                assert bridge is not None
            elif path in {"/empty", "/restore"}:
                for name, _, _, state in rows:
                    history.set_state(
                        name,
                        "stopped" if path == "/empty" else state,
                        now=int(time.time() * 1000),
                    )
            elif path == "/notice":
                server.bus.publish(
                    {
                        "event_type": "Notification",
                        "notification_type": "permission_prompt",
                        "session_key": "payments-review",
                        "test": True,
                    }
                )
            elif path == "/quit":
                stopped.set()
            else:
                raise ValueError("Unknown fixture command")
            body = result.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nCache-Control: no-store\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    listener = await asyncio.start_server(control, "127.0.0.1", 0, limit=8192)
    print(f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}", flush=True)
    try:
        await stopped.wait()
    finally:
        listener.close()
        await listener.wait_closed()
        await bridge.close()
        history.purge_test_sessions()
        history.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="ducky-ui-test-") as directory:
        asyncio.run(run(Path(directory)))
