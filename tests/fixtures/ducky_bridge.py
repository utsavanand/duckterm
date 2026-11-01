"""A disposable TLS host for the native client integration test. Never uses real state."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from duckterm.editions import LITE
from duckterm.mobile.bridge import start
from duckterm.mobile.cli import configure, invitation
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


async def run(root: Path) -> None:
    os.environ["DUCKTERM_HOME"] = str(root)
    os.environ["DUCKTERM_TMUX_SOCKET"] = "ducky-native-test-" + str(os.getpid())
    os.environ["DUCKTERM_SUMMARIZER"] = "off"
    configure(root, "127.0.0.1", 4430)
    path = root / "ducky-bridge.json"
    config = json.loads(path.read_text())
    config["port"] = 0
    path.write_text(json.dumps(config))
    history = HistoryStore(root / "state.sqlite", edition=LITE)
    server = Server(history=history, edition=LITE)
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
    bridge = await start(server)
    assert bridge is not None and bridge.listener is not None
    config["port"] = bridge.listener.sockets[0].getsockname()[1]
    path.write_text(json.dumps(config))
    print(invitation(root), flush=True)
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            operation, identifier = line.strip().split(" ", 1)
            if operation == "approve":
                bridge.grants.approve(identifier)
            elif operation == "revoke":
                bridge.grants.revoke(identifier)
            else:
                raise ValueError("Unknown fixture action")
            print("ok", flush=True)
    finally:
        await bridge.close()
        history.purge_test_sessions()
        history.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="ducky-native-test-") as directory:
        asyncio.run(run(Path(directory)))
