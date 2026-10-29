"""Disposable real-server fixture with a harmless process standing in for SSH."""

import asyncio
import json
import os
import sys
from pathlib import Path

from duckterm import cli
from duckterm import server as server_module
from duckterm.collaboration.transport import Transport

root = Path(os.environ["HOME"])


class FixtureServer(server_module.Server):
    async def serve(self, host, port, on_listening=None):
        transport = Transport("synthetic-coordinator")
        transport.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        pid = transport.process.pid
        original_close = transport.close

        async def delayed_close():
            (root / "closing").touch()
            async with asyncio.timeout(10):
                while not (root / "finish-close").exists():
                    await asyncio.sleep(0.01)
            await original_close()

        transport.close = delayed_close
        self.collaboration.transport = transport

        async def no_agents():
            return []

        self.orchestrator.reconcile = no_agents

        def listening(host, port):
            (root / "ready.json").write_text(json.dumps({"tunnel_pid": pid}))

        await super().serve(host, port, on_listening=listening)


server_module.Server = FixtureServer
raise SystemExit(cli._serve("127.0.0.1", 0))
