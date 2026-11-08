"""Private setup-process transport. Never creates a DuckTerm agent or log."""

from __future__ import annotations

import base64
import contextlib
import errno
import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

FRAME_LIMIT = 65536
INPUT_LIMIT = 32768
CAPTURE_LIMIT = 16384
CLEANUP_GRACE = 0.25
Emit = Callable[[dict[str, Any]], None]

# A fresh interpreter, rather than preexec_fn in a possibly threaded process,
# acquires the controlling terminal before replacing itself with the fixed CLI.
_PTY_EXEC = (
    "import fcntl,os,sys,termios; "
    "fcntl.ioctl(0,termios.TIOCSCTTY,0); "
    "os.execvpe(sys.argv[1],sys.argv[1:],os.environ)"
)


class SetupFailure(Exception):
    """Only fixed internal codes cross the native protocol boundary."""


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


class Control:
    def __init__(self, fd: int | None = None, cancelled: threading.Event | None = None):
        self.fd = fd
        self.cancelled = cancelled or threading.Event()
        self.partial = bytearray()
        self.pending = bytearray()

    def poll(self, terminal: int | None = None) -> None:
        if self.cancelled.is_set():
            raise SetupFailure("cancelled")
        if self.fd is None or not select.select([self.fd], [], [], 0)[0]:
            return
        chunk = os.read(self.fd, 4096)
        if not chunk:
            raise SetupFailure("cancelled")
        self.partial.extend(chunk)
        if len(self.partial) > FRAME_LIMIT:
            raise SetupFailure("invalid_command")
        while b"\n" in self.partial:
            line, _, rest = self.partial.partition(b"\n")
            self.partial = bytearray(rest)
            try:
                command = json.loads(line, object_pairs_hook=unique_object)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise SetupFailure("invalid_command") from exc
            if not isinstance(command, dict):
                raise SetupFailure("invalid_command")
            if command == {"type": "cancel"}:
                raise SetupFailure("cancelled")
            if terminal is None:
                raise SetupFailure("invalid_command")
            if set(command) == {"type", "data"} and command["type"] == "input":
                try:
                    data = base64.b64decode(command["data"], validate=True)
                except (ValueError, TypeError) as exc:
                    raise SetupFailure("invalid_command") from exc
                if len(data) > INPUT_LIMIT or len(self.pending) + len(data) > FRAME_LIMIT:
                    raise SetupFailure("invalid_command")
                self.pending.extend(data)
            elif set(command) == {"type", "cols", "rows"} and command["type"] == "resize":
                cols, rows = command["cols"], command["rows"]
                if type(cols) is not int or type(rows) is not int:
                    raise SetupFailure("invalid_command")
                if not (1 <= cols <= 500 and 1 <= rows <= 300):
                    raise SetupFailure("invalid_command")
                fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            else:
                raise SetupFailure("invalid_command")


class ProtocolWriter:
    """A stalled native reader must not strand an installer indefinitely."""

    def __init__(self, fd: int):
        self.fd = fd
        os.set_blocking(fd, False)

    def __call__(self, event: dict[str, Any]) -> None:
        data = json.dumps({"protocol": 1, **event}, separators=(",", ":")).encode() + b"\n"
        if len(data) > FRAME_LIMIT:
            raise SetupFailure("transport_unavailable")
        deadline = time.monotonic() + 1.0
        try:
            while data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [self.fd], [], remaining)[1]:
                    raise SetupFailure("transport_unavailable")
                try:
                    written = os.write(self.fd, data)
                except BlockingIOError:
                    continue
                if written <= 0:
                    raise SetupFailure("transport_unavailable")
                data = data[written:]
        except OSError as exc:
            raise SetupFailure("transport_unavailable") from exc


def _exited(child: subprocess.Popen[bytes]) -> bool:
    # WNOWAIT preserves the leader (and hence its PID/PGID) until all group
    # cleanup is finished. Popen.poll()/wait() would release it too early.
    return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def _kill_group(child: subprocess.Popen[bytes], sig: int) -> None:
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin excludes zombies from killpg's eligible members and returns
        # EPERM for a zombie-only group. Do not hide a real signal denial: the
        # leader must be unreaped/exited and a group-only state read must show
        # no live members. No argv, environment or owner process data is read.
        # XNU: bsd/kern/kern_sig.c, killpg1's SZOMB filter/nfound result.
        if not _darwin_group_finished(child):
            raise SetupFailure("cleanup_failed") from None


def _darwin_group_finished(child: subprocess.Popen[bytes]) -> bool:
    if sys.platform != "darwin" or not _exited(child):
        return False
    try:
        states = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-g", str(child.pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
            timeout=0.5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    rows = states.stdout.split()
    return (
        len(states.stdout) <= 4096
        and states.returncode in (0, 1)
        and all(row.startswith(b"Z") for row in rows)
        and (states.returncode == 0 or not rows)
    )


def _cleanup(child: subprocess.Popen[bytes], *, failed: bool) -> int:
    try:
        if failed:
            # Still try the final KILL; its failure remains fatal.
            with contextlib.suppress(SetupFailure):
                _kill_group(child, signal.SIGTERM)
            time.sleep(CLEANUP_GRACE)
        _kill_group(child, signal.SIGKILL)
        return child.wait(timeout=1.0)
    except subprocess.TimeoutExpired as exc:
        raise SetupFailure("cleanup_failed") from exc
    finally:
        # Even an OS-denied group signal must not leave our exited leader as a
        # zombie. Reap only after all attempts to use its anchored PGID end.
        if child.returncode is None and _exited(child):
            child.wait(timeout=1.0)


def run_command(
    argv: list[str],
    environment: Mapping[str, str],
    cwd: str,
    *,
    control: Control,
    timeout: float,
    interactive: bool = False,
    emit: Emit | None = None,
    capture_limit: int = CAPTURE_LIMIT,
) -> tuple[int, bytes]:
    """Internal primitive; only setup.py's fixed action table may call this."""
    child: subprocess.Popen[bytes] | None = None
    master: int | None = None
    slave: int | None = None
    failed = True
    output = bytearray()
    code = -1
    try:
        if interactive:
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        # Native can send initial geometry immediately after the ready frame,
        # before spawn. Accept it against the new PTY, not a status-only parser.
        control.poll(master)
        child = subprocess.Popen(
            ([sys.executable, "-I", "-B", "-c", _PTY_EXEC, *argv] if interactive else argv),
            cwd=cwd,
            env=dict(environment),
            stdin=slave if interactive else subprocess.DEVNULL,
            stdout=slave if interactive else subprocess.PIPE,
            stderr=slave if interactive else subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        if slave is not None:
            os.close(slave)
            slave = None
        source = master if master is not None else child.stdout.fileno() if child.stdout else -1
        os.set_blocking(source, False)
        deadline = time.monotonic() + timeout
        eof = False
        finished = False
        while True:
            control.poll(master)
            if time.monotonic() >= deadline:
                raise SetupFailure("timeout")
            if not finished and _exited(child):
                finished = True
                # A wrapper can exit while a grandchild keeps its pipe/PTY.
                # Stop that group before draining/reaping the anchored leader.
                _kill_group(child, signal.SIGKILL)
            if finished and eof:
                failed = False
                break
            readable, writable, _ = select.select(
                [] if eof else [source],
                [source] if master is not None and control.pending and not finished else [],
                [],
                0.05,
            )
            if writable:
                try:
                    written = os.write(source, control.pending)
                    del control.pending[:written]
                except BlockingIOError:
                    pass
            if readable:
                try:
                    chunk = os.read(source, 4096)
                except OSError as exc:
                    if master is not None and exc.errno == errno.EIO:
                        chunk = b""  # PTY EOF on Darwin/Linux.
                    elif exc.errno == errno.EAGAIN:
                        continue
                    else:
                        raise
                if not chunk:
                    eof = True
                elif interactive:
                    if emit is not None:
                        emit({"type": "output", "data": base64.b64encode(chunk).decode("ascii")})
                else:
                    output.extend(chunk)
                    if len(output) > capture_limit:
                        raise SetupFailure("output_limit")
    finally:
        try:
            if child is not None:
                code = _cleanup(child, failed=failed)
        finally:
            if child is not None and child.stdout is not None:
                child.stdout.close()
            if master is not None:
                os.close(master)
            if slave is not None:
                os.close(slave)
    return code, bytes(output)
