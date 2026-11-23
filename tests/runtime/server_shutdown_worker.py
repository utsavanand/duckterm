"""Disposable real-server fixture with a harmless process standing in for SSH."""

import asyncio
import faulthandler
import json
import os
import sys
import threading
from pathlib import Path

from duckterm import cli
from duckterm import server as server_module
from duckterm.collaboration.transport import Transport

root = Path(os.environ["HOME"])
mode = os.environ.get("TEST_SHUTDOWN_MODE", "normal")


def forbidden_dump_timer(*args, **kwargs):
    raise AssertionError("Server must not start or join a faulthandler dump timer")


# A native dump can fail to finish. Even cancellation then waits indefinitely;
# catch any attempt to use either API throughout real CLI startup and shutdown.
faulthandler.dump_traceback_later = forbidden_dump_timer
faulthandler.cancel_dump_traceback_later = forbidden_dump_timer


class FixtureServer(server_module.Server):
    async def serve(self, host, port, on_listening=None, **kwargs):
        transport = Transport("synthetic-coordinator")
        transport.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import signal,time,pathlib; "
            + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if mode != "normal" else "")
            + f"pathlib.Path({str(root / 'child-ready')!r}).touch(); time.sleep(60)",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        pid = transport.process.pid
        while not (root / "child-ready").exists():
            await asyncio.sleep(0.01)
        original_close = transport.close

        async def delayed_close():
            (root / "closing").touch()
            async with asyncio.timeout(10):
                while not (root / "finish-close").exists():
                    await asyncio.sleep(0.01)
            await original_close()

        if mode == "normal":
            transport.close = delayed_close
        if mode in {"mobile_stall", "executor_stall", "legacy_stall"}:
            from duckterm.mobile.messages import Messages

            async def stuck_close(self):
                (root / "closing").touch()
                await asyncio.Event().wait()

            Messages.close = stuck_close
        if mode in {"executor_stall", "legacy_stall"}:
            asyncio.create_task(asyncio.to_thread(threading.Event().wait))
        self.collaboration.transport = transport

        async def no_agents():
            return []

        self.orchestrator.reconcile = no_agents

        def listening(host, port):
            (root / "ready.json").write_text(
                json.dumps({"tunnel_pid": pid, "port": self._bound_port, "pid": os.getpid()})
            )

        await super().serve(host, port, on_listening=listening, **kwargs)


if mode == "legacy_stall":
    # Emulate the published server's missing shutdown deadline.
    class NoDeadline:
        def __init__(self, *args):
            pass

        def start(self):
            pass

        def cancel(self):
            pass

    threading.Timer = NoDeadline

server_module.Server = FixtureServer
port = int(os.environ.get("TEST_RESTART_PORT", "0"))
if os.environ.get("TEST_DO_RESTART"):
    raise SystemExit(cli._restart("127.0.0.1", port))
raise SystemExit(cli._serve("127.0.0.1", port))
