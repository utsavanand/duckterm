"""JSONL for the native Mac app's account screens.

Same transport as the pairing window (pairing_stream): frames on stdout,
commands on stdin, stdin EOF cancels. Three ceremonies:

- signin: GitHub's device flow, showing the code in the app.
- claims: the account and the phones on it waiting for this Mac, re-sent
  whenever either changes, so the app can post a notification.
- claim: the owner allows or denies one waiting phone.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any

from duckterm.mobile import account
from duckterm.mobile.grants import Denied, Grants, capabilities
from duckterm.mobile.pairing_stream import (
    CeremonyError,
    Commands,
    cancellation,
    commands,
    emit,
    validate_analysis_scopes,
)

WATCH_SECONDS = 1.0


class Cancelled(Exception):
    pass


def _wait(incoming: Commands, seconds: float) -> None:
    """Sleep between GitHub polls, but end the sign-in as soon as the window closes."""
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        command = incoming.poll(remaining)
        if command is None:
            continue
        if command["action"] == "cancel":
            raise Cancelled
        raise CeremonyError("invalid_command")


def _run(body: Any) -> int:
    try:
        with cancellation(), commands() as incoming:
            return int(body(incoming))
    except BrokenPipeError:
        return 1
    except (KeyboardInterrupt, Cancelled):
        with contextlib.suppress(BrokenPipeError):
            emit("cancelled")
        return 130
    except account.AccountError as exc:
        with contextlib.suppress(BrokenPipeError):
            emit("error", code=exc.code)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        code = str(exc) if isinstance(exc, CeremonyError) else "pairing_failed"
        with contextlib.suppress(BrokenPipeError):
            emit("error", code=code)
        return 1


def signin_json(directory: Path, name: str) -> int:
    def body(incoming: Commands) -> int:
        signed_in = account.sign_in(
            directory,
            name,
            say=lambda code, uri: emit("code", user_code=code, verification_uri=uri),
            sleep=lambda seconds: _wait(incoming, seconds),
        )
        emit("signed_in", login=signed_in["login"])
        return 0

    return _run(body)


def _waiting(grants: Grants) -> list[dict[str, Any]]:
    """Account claims still waiting for the owner, as the app shows them."""
    return [
        {
            "id": device["id"],
            "name": device["name"],
            "expires_at": device["expires_at"],
            "login": device["account"]["login"],
            "code": device["code"],
        }
        for device in grants.devices()
        if device["status"] == "pending" and device.get("account") and device.get("code")
    ]


def _stamp(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def claims_json(directory: Path) -> int:
    grants = Grants(directory)

    def body(incoming: Commands) -> int:
        stamps: tuple[int | None, int | None] | None = None
        login: str | None = None
        waiting: list[dict[str, Any]] = []
        sent: dict[str, Any] | None = None
        while True:
            # Reread only when a file changed; the app keeps this running.
            current = (_stamp(directory / account.ACCOUNT_FILE), _stamp(grants.path))
            if current != stamps:
                stamps = current
                signed_in = account.load(directory)
                login = signed_in["login"] if signed_in else None
                waiting = _waiting(grants)
            now = time.time()
            frame = {"login": login, "devices": [d for d in waiting if d["expires_at"] > now]}
            if frame != sent:
                emit("claims", **frame)
                sent = frame
            command = incoming.poll(WATCH_SECONDS)
            if command is None:
                continue
            if command["action"] == "cancel":
                emit("cancelled")
                return 0
            raise CeremonyError("invalid_command")

    return _run(body)


def claim_json(directory: Path, device_id: str) -> int:
    grants = Grants(directory)

    def offer() -> dict[str, Any] | None:
        return next((d for d in _waiting(grants) if d["id"] == device_id), None)

    def body(incoming: Commands) -> int:
        offered = offer()
        if offered is None or offered["expires_at"] <= time.time():
            emit("expired", phase="pending")
            return 1
        emit("pending", device=offered)
        while True:
            if time.time() >= offered["expires_at"]:
                emit("expired", phase="pending")
                return 1
            if offer() != offered:
                # Removed or replaced while the window was open.
                raise CeremonyError("approval_unavailable")
            command = incoming.poll(min(0.25, offered["expires_at"] - time.time()))
            if command is None:
                continue
            if command["action"] == "cancel":
                # The phone keeps waiting; the notification can reopen this.
                emit("cancelled")
                return 0
            if command["device_id"] != device_id or offer() != offered:
                raise CeremonyError("approval_unavailable")
            identity = {"id": device_id, "name": offered["name"]}
            if command["action"] == "deny":
                grants.revoke(device_id)
                emit("denied", device=identity)
                return 0
            try:
                approved = capabilities(command.get("capabilities"), strict=True)
                validate_analysis_scopes(directory, approved)
                grants.approve(device_id, capabilities=approved)
            except Denied as exc:
                raise CeremonyError("approval_unavailable") from exc
            emit(
                "approved",
                device={**identity, "capabilities": approved, "authorization_revision": 1},
            )
            return 0

    return _run(body)
