"""Single-use invitations and explicitly approved phone grants, separate from SQLite."""

import contextlib
import fcntl
import hashlib
import json
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


class Denied(ValueError):
    pass


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Grants:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "ducky-grants.json"

    @contextlib.contextmanager
    def locked(self, *, write: bool = False) -> Iterator[dict[str, Any]]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(
            self.directory / ".ducky-grants.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = private_read(self.path)
            state = (
                json.loads(raw)
                if raw
                else {"version": 1, "host_id": uuid.uuid4().hex, "invites": {}, "devices": {}}
            )
            if not isinstance(state, dict) or state.get("version") != 1:
                raise ValueError("Invalid Ducky grant store")
            yield state
            if write:
                private_write(self.path, json.dumps(state))
        finally:
            os.close(fd)

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
                    return {
                        "id": identifier,
                        "name": device["name"],
                        "status": device["status"],
                        "host_id": state["host_id"],
                    }
        raise Denied("Device is unapproved, expired, or revoked")

    def devices(self) -> list[dict[str, Any]]:
        with self.locked() as state:
            return [
                {
                    "id": key,
                    **{
                        field: value[field]
                        for field in ("name", "status", "created_at", "expires_at")
                    },
                }
                for key, value in state["devices"].items()
            ]

    def approve(self, identifier: str, *, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        with self.locked(write=True) as state:
            device = state["devices"].get(identifier)
            if not device or device["status"] != "pending" or device["expires_at"] <= now:
                raise Denied("No unexpired pending device with that identifier")
            device["status"] = "active"
            device["expires_at"] = 0

    def revoke(self, identifier: str) -> None:
        with self.locked(write=True) as state:
            state["devices"].pop(identifier, None)
