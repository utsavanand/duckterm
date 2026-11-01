"""Private JSONL ceremony for the native Mac pairing window.

The process inherits the selected local workspace, never a session credential.
This is a same-user CLI transport, not an HTTP approval endpoint. The Mac app
performs owner authentication before sending an explicit approval command.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from duckterm.mobile.cli import invitation_link, invitation_payload
from duckterm.mobile.grants import Denied, Grants
from duckterm.mobile.pairing import pairing_lock, prepare, wait_for_listener


class CeremonyError(ValueError):
    pass


def emit(state: str, **fields: Any) -> None:
    print(json.dumps({"version": 1, "state": state, **fields}), flush=True)


class Commands:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.buffer = bytearray()

    def poll(self, timeout: float) -> dict[str, Any] | None:
        if b"\n" not in self.buffer:
            try:
                if not select.select([self.fd], [], [], max(0, timeout))[0]:
                    return None
                data = os.read(self.fd, 4096 - len(self.buffer))
            except (BlockingIOError, InterruptedError):
                return None
            if not data:
                return {"action": "cancel"}
            self.buffer.extend(data)
        if len(self.buffer) >= 4096:
            raise CeremonyError("invalid_command")
        if b"\n" not in self.buffer:
            return None
        raw, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        try:
            command = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise CeremonyError("invalid_command") from exc
        if not isinstance(command, dict):
            raise CeremonyError("invalid_command")
        action = command.get("action")
        if action == "cancel" and set(command) == {"action"}:
            return command
        if (
            action not in ("approve", "deny")
            or set(command) != {"action", "device_id"}
            or not isinstance(command["device_id"], str)
        ):
            raise CeremonyError("invalid_command")
        return command


@contextlib.contextmanager
def commands() -> Iterator[Commands]:
    fd = sys.stdin.fileno()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        yield Commands(fd)
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)


@contextlib.contextmanager
def cancellation() -> Iterator[None]:
    def stop(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    # Process.terminate() from the native owner must still unwind the invitation
    # cleanup. The prior handler is restored for callers sharing an interpreter.
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def ceremony(directory: Path, incoming: Commands) -> int:
    payload = invitation_payload(directory)
    grants = Grants(directory)
    deadline = time.monotonic() + min(600, max(0, payload["expires_at"] - time.time()))
    offered: dict[str, Any] | None = None
    try:
        emit("invitation", link=invitation_link(payload), expires_at=payload["expires_at"])
        while True:
            remaining = min(payload["expires_at"] - time.time(), deadline - time.monotonic())
            if remaining <= 0:
                emit("expired", phase="pending" if offered else "invitation")
                return 1
            pending = grants.pending_invitation(payload["invite"])
            if offered and (not pending or pending["id"] != offered["id"]):
                raise CeremonyError("approval_unavailable")
            if pending and not offered:
                offered = {
                    **pending,
                    "expires_at": min(pending["expires_at"], payload["expires_at"]),
                }
                emit("pending", device=offered)
            command = incoming.poll(min(0.25, remaining))
            if command is None:
                continue
            if command["action"] == "cancel":
                emit("cancelled")
                return 0
            if not offered or command["device_id"] != offered["id"]:
                raise CeremonyError("invalid_command")
            if time.time() >= offered["expires_at"] or time.monotonic() >= deadline:
                emit("expired", phase="pending")
                return 1
            # Re-read after input: a device may have been revoked while the Mac
            # authenticated its owner. Approval also rechecks under the store lock.
            current = grants.pending_invitation(payload["invite"])
            if not current or current["id"] != offered["id"]:
                raise CeremonyError("approval_unavailable")
            if command["action"] == "approve":
                try:
                    grants.approve_invitation(payload["invite"], offered["id"])
                except Denied as exc:
                    raise CeremonyError("approval_unavailable") from exc
                emit("approved", device={"id": offered["id"], "name": offered["name"]})
            else:
                emit("denied", device={"id": offered["id"], "name": offered["name"]})
            return 0
    finally:
        grants.cancel_invitation(payload["invite"])


def pair_json(
    directory: Path, *, selected_address: str | None = None, port: int | None = None
) -> int:
    try:
        # Keep this lock for the whole process ceremony so two Mac windows or a
        # terminal and a window cannot offer competing invitations accidentally.
        with cancellation(), commands() as incoming, pairing_lock(directory):
            try:
                config = prepare(directory, selected_address=selected_address, port=port)
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                raise CeremonyError("setup_unavailable") from exc
            try:
                wait_for_listener(directory, config)
            except (OSError, ValueError) as exc:
                raise CeremonyError("listener_unavailable") from exc
            # A closed native window during setup must not create an invitation.
            command = incoming.poll(0)
            if command is not None:
                if command["action"] != "cancel":
                    raise CeremonyError("invalid_command")
                emit("cancelled")
                return 0
            return ceremony(directory, incoming)
    except BrokenPipeError:
        return 1
    except KeyboardInterrupt:
        with contextlib.suppress(BrokenPipeError):
            emit("cancelled")
        return 130
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        code = str(exc) if isinstance(exc, CeremonyError) else "pairing_failed"
        if isinstance(exc, ValueError) and str(exc).startswith("Another Ducky pairing"):
            code = "pairing_busy"
        with contextlib.suppress(BrokenPipeError):
            emit("error", code=code)
        return 1
