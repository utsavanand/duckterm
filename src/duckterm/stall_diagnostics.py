"""Capture Python stacks when the CLI server's event loop stops making progress.

The CLI owns faulthandler's process-wide timer. Its native watchdog can dump
stacks even while Python is stuck in a C extension holding the GIL. Ordinary
awaits keep the heartbeat running and do not produce a stall report.
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from duckterm import __version__

STALL_SECONDS = 10.0
MAX_LOG_BYTES = 1024 * 1024


def _open_log(path: Path) -> BinaryIO:
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("stall log must be a regular file")
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "wb", buffering=0)
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def watch_stalls(path: Path, *, timeout: float = STALL_SECONDS) -> Iterator[None]:
    """One native dump per stall; cancel before closing its file on every exit.

    The private log rotates between dumps at 1 MiB, keeping one previous log.
    One dump can exceed that boundary (faulthandler bounds threads and frames).
    No locals, SQL values, transcript contents or request bodies are recorded.
    Diagnostic failures must not stop the server.
    """
    loop = asyncio.get_running_loop()
    try:
        output = _open_log(path)
    except OSError:
        print("[duckterm] server stall diagnostics unavailable", file=sys.stderr)
        yield
        return

    handle: asyncio.TimerHandle | None = None
    last = loop.time()

    def arm() -> None:
        nonlocal handle, last, output
        # The previous timer may still be writing. Join it before rotating or
        # touching the file; cancellation is also required before file close.
        faulthandler.cancel_dump_traceback_later()
        now = loop.time()
        recovered = now - last >= timeout
        last = now
        try:
            rotated = os.fstat(output.fileno()).st_size >= MAX_LOG_BYTES
            if rotated:
                output.close()
                path.replace(path.with_suffix(".previous.log"))
                output = _open_log(path)
            if handle is None or recovered or rotated:
                label = (
                    "event loop resumed after stall or system suspension" if recovered else "armed"
                )
                output.write(
                    f"\n{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                    f"DuckTerm {__version__} pid={os.getpid()}: {label}; "
                    f"threshold={timeout:g}s\n".encode()
                )
            faulthandler.dump_traceback_later(timeout, file=output, repeat=False, exit=False)
        except (OSError, RuntimeError):
            print("[duckterm] server stall diagnostics unavailable", file=sys.stderr)
            return
        handle = loop.call_later(timeout / 4, arm)

    try:
        arm()
        yield
    finally:
        if handle is not None:
            handle.cancel()
        faulthandler.cancel_dump_traceback_later()
        output.close()
