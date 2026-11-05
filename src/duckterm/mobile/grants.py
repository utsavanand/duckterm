"""Single-use invitations and explicitly approved phone grants, separate from SQLite."""

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from duckterm.helpers.private_files import private_read, private_write

TOKEN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
CAPABILITIES = frozenset({"status:read", "conversation:read", "session:send"})


class Denied(ValueError):
    pass


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def capabilities(value: object = None, *, strict: bool = False) -> list[str]:
    """Legacy or damaged scopes never imply control; approvals reject bad input."""
    if value is None:
        return ["status:read"]
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or item not in CAPABILITIES for item in value)
        or len(set(value)) != len(value)
    ):
        if strict:
            raise ValueError("Invalid Ducky capabilities")
        return ["status:read"]
    return sorted({"status:read", *value})


def _public_device(identifier: str, device: dict[str, Any], host_id: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": device["name"],
        "status": device["status"],
        "host_id": host_id,
        "capabilities": (
            capabilities(device.get("capabilities")) if device["status"] == "active" else []
        ),
        "api_version": 2,
    }


class Grants:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "ducky-grants.json"

    @contextlib.contextmanager
    def locked(
        self, *, write: bool = False, timeout: float | None = None
    ) -> Iterator[dict[str, Any]]:
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("Invalid grant lock timeout")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(
            self.directory / ".ducky-grants.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            if timeout is None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("Ducky grant store is busy") from None
                        time.sleep(min(0.01, remaining))
            raw = private_read(self.path)
            state = (
                json.loads(raw)
                if raw
                else {"version": 1, "host_id": uuid.uuid4().hex, "invites": {}, "devices": {}}
            )
            if (
                not isinstance(state, dict)
                or state.get("version") != 1
                or not isinstance(state.get("host_id"), str)
                or not state["host_id"]
                or any(
                    not isinstance(state.get(section), dict)
                    or any(
                        not isinstance(key, str) or not isinstance(value, dict)
                        for key, value in state[section].items()
                    )
                    for section in ("invites", "devices")
                )
            ):
                raise ValueError("Invalid Ducky grant store")
            yield state
            if write:
                private_write(self.path, json.dumps(state))
        finally:
            os.close(fd)

    def reset(self) -> None:
        """Explicit identity replacement invalidates every old invitation and device."""
        with self.locked(write=True) as state:
            state.clear()
            state.update(version=1, host_id=uuid.uuid4().hex, invites={}, devices={})

    def invite(self, *, now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        secret = secrets.token_urlsafe(32)
        with self.locked(write=True) as state:
            state["invites"] = {
                key: value for key, value in state["invites"].items() if value["expires_at"] > now
            }
            if len(state["invites"]) >= 16:
                raise ValueError("Too many pending Ducky invitations")
            state["invites"][digest(secret)] = {"expires_at": now + 600}
            return {"host_id": state["host_id"], "invite": secret, "expires_at": now + 600}

    def claim(
        self, invite: str, token: str, name: str, *, now: int | None = None
    ) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        if not TOKEN.fullmatch(invite) or not TOKEN.fullmatch(token):
            raise Denied("Invalid pairing credential")
        if not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("Device name must be 1–80 printable characters")
        with self.locked(write=True) as state:
            grant = state["invites"].get(digest(invite))
            if not grant or grant["expires_at"] <= now:
                raise Denied("Invitation expired or unavailable")
            token_hash = digest(token)
            if identifier := grant.get("claimed_by"):
                device = state["devices"].get(identifier)
                if not device or not secrets.compare_digest(device["token_hash"], token_hash):
                    raise Denied("Invitation has already been used")
                return {"id": identifier, "status": device["status"], "host_id": state["host_id"]}
            state["devices"] = {
                key: value
                for key, value in state["devices"].items()
                if value["status"] != "pending" or value["expires_at"] > now
            }
            if len(state["devices"]) >= 32:
                raise ValueError("Remove an old Ducky device before pairing another")
            identifier = uuid.uuid4().hex
            state["devices"][identifier] = {
                "name": name.strip(),
                "token_hash": token_hash,
                "status": "pending",
                "created_at": now,
                "expires_at": now + 600,
                "last_seen_at": None,
            }
            grant["claimed_by"] = identifier
            return {"id": identifier, "status": "pending", "host_id": state["host_id"]}

    def authenticate(
        self, token: str, *, pending: bool = False, now: int | None = None
    ) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        if not TOKEN.fullmatch(token):
            raise Denied("Ducky device credential required")
        with self.locked() as state:
            for identifier, device in state["devices"].items():
                if not secrets.compare_digest(device["token_hash"], digest(token)):
                    continue
                active = device["status"] == "active"
                awaiting = pending and device["status"] == "pending" and device["expires_at"] > now
                if active or awaiting:
                    # A foreground viewer polls frequently. Persist at most once
                    # a minute per device, under the same lock as revocation.
                    seen = device.get("last_seen_at")
                    if seen is None or now - seen >= 60:
                        device["last_seen_at"] = now
                        private_write(self.path, json.dumps(state))
                    return _public_device(identifier, device, state["host_id"])
        raise Denied("Device is unapproved, expired, or revoked")

    def authenticated_by_id(
        self,
        identifier: str,
        required: str,
        *,
        host_id: str | None = None,
        lock_timeout: float = 1.0,
    ) -> dict[str, Any]:
        """Recheck a previously authenticated queued owner; not HTTP authentication."""
        with self.authorized_device(
            identifier, required, host_id=host_id, lock_timeout=lock_timeout
        ) as device:
            return device

    @contextlib.contextmanager
    def authorized_device(
        self,
        identifier: str,
        required: str,
        *,
        host_id: str | None = None,
        lock_timeout: float = 1.0,
    ) -> Iterator[dict[str, Any]]:
        """Order a short scoped operation against revoke; never yield secret state."""
        if required not in CAPABILITIES:
            raise Denied("Ducky capability is unavailable")
        with self.locked(timeout=lock_timeout) as state:
            if host_id is not None and host_id != state["host_id"]:
                raise Denied("Ducky host identity changed")
            device = state["devices"].get(identifier)
            if device and device.get("status") == "active":
                public = _public_device(identifier, device, state["host_id"])
                if required in public["capabilities"]:
                    yield public
                    return
        raise Denied("Ducky device does not have this capability")

    def devices(self) -> list[dict[str, Any]]:
        with self.locked() as state:
            return [
                {
                    "id": key,
                    **{
                        field: value[field]
                        for field in ("name", "status", "created_at", "expires_at")
                    },
                    "last_seen_at": value.get("last_seen_at"),
                    "capabilities": (
                        capabilities(value.get("capabilities"))
                        if value["status"] == "active"
                        else []
                    ),
                }
                for key, value in state["devices"].items()
            ]

    def approve(
        self,
        identifier: str,
        *,
        now: int | None = None,
        capabilities: list[str] | None = None,
    ) -> None:
        approved = _approval_capabilities(capabilities)
        now = int(time.time()) if now is None else now
        with self.locked(write=True) as state:
            device = state["devices"].get(identifier)
            if not device or device["status"] != "pending" or device["expires_at"] <= now:
                raise Denied("No unexpired pending device with that identifier")
            device["status"] = "active"
            device["expires_at"] = 0
            device["capabilities"] = approved

    def pending_invitation(self, invite: str, *, now: int | None = None) -> dict[str, Any] | None:
        """Only the phone which claimed this ceremony's invitation may be offered."""
        now = int(time.time()) if now is None else now
        with self.locked() as state:
            invitation = state["invites"].get(digest(invite))
            if not invitation or invitation["expires_at"] <= now:
                return None
            identifier = invitation.get("claimed_by")
            device = state["devices"].get(identifier)
            if not device or device["status"] != "pending" or device["expires_at"] <= now:
                return None
            return {"id": identifier, "name": device["name"], "expires_at": device["expires_at"]}

    def approve_invitation(
        self,
        invite: str,
        identifier: str,
        *,
        now: int | None = None,
        capabilities: list[str] | None = None,
    ) -> None:
        approved = _approval_capabilities(capabilities)
        now = int(time.time()) if now is None else now
        with self.locked(write=True) as state:
            invitation = state["invites"].get(digest(invite))
            device = state["devices"].get(identifier)
            if (
                not invitation
                or invitation["expires_at"] <= now
                or invitation.get("claimed_by") != identifier
                or not device
                or device["status"] != "pending"
                or device["expires_at"] <= now
            ):
                raise Denied("No unexpired pending device for this invitation")
            device.update(status="active", expires_at=0, capabilities=approved)

    def cancel_invitation(self, invite: str) -> None:
        with self.locked(write=True) as state:
            invitation = state["invites"].pop(digest(invite), None)
            if invitation:
                identifier = invitation.get("claimed_by")
                device = state["devices"].get(identifier)
                if device and device["status"] == "pending":
                    state["devices"].pop(identifier)

    def revoke(self, identifier: str) -> None:
        with self.locked(write=True) as state:
            state["devices"].pop(identifier, None)


def _approval_capabilities(value: list[str] | None) -> list[str]:
    return capabilities(value, strict=True)
