"""SQLite storage for accounts, signed-in devices and registered Macs.

Session tokens and one-time sign-in codes are stored only as SHA-256 hashes.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

SESSION_IDLE_SECONDS = 90 * 24 * 3600
SIGN_IN_SECONDS = 300
MAX_DEVICES = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    github_id INTEGER NOT NULL UNIQUE,
    login TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('mac', 'phone')),
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS macs (
    device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    host_id TEXT NOT NULL,
    certificate_sha256 TEXT NOT NULL,
    tls_name TEXT,
    url TEXT NOT NULL,
    ticket_secret BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS sign_ins (
    state TEXT PRIMARY KEY,
    challenge TEXT NOT NULL,
    code_hash TEXT UNIQUE,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
"""


class TooManyDevices(ValueError):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path: Path) -> None:
        # The file holds every Mac's ticket secret. SQLite gives the -wal and -shm
        # files the database's own mode, so creating it 0600 covers all three.
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def account(self, github_id: int, login: str, *, now: int | None = None) -> str:
        """The account for a GitHub user, created on first sign-in; logins can change."""
        now = int(time.time()) if now is None else now
        row = self.db.execute(
            "SELECT id FROM accounts WHERE github_id = ?", (github_id,)
        ).fetchone()
        if row:
            self.db.execute("UPDATE accounts SET login = ? WHERE id = ?", (login, row["id"]))
            return str(row["id"])
        identifier = secrets.token_hex(16)
        self.db.execute(
            "INSERT INTO accounts (id, github_id, login, created_at) VALUES (?, ?, ?, ?)",
            (identifier, github_id, login, now),
        )
        return identifier

    def add_device(
        self, account_id: str, kind: str, name: str, *, now: int | None = None
    ) -> tuple[str, str]:
        """A new signed-in device and its session token, shown only this once."""
        now = int(time.time()) if now is None else now
        count = self.db.execute(
            "SELECT COUNT(*) FROM devices WHERE account_id = ?", (account_id,)
        ).fetchone()[0]
        if count >= MAX_DEVICES:
            raise TooManyDevices()
        identifier, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO devices (id, account_id, kind, name, token_hash, created_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (identifier, account_id, kind, name, digest(token), now, now),
        )
        return identifier, token

    def authenticate(self, token: str, *, now: int | None = None) -> dict[str, Any] | None:
        now = int(time.time()) if now is None else now
        row = self.db.execute(
            "SELECT d.id, d.account_id, d.kind, d.name, d.last_seen_at, a.login FROM devices d"
            " JOIN accounts a ON a.id = d.account_id WHERE d.token_hash = ?",
            (digest(token),),
        ).fetchone()
        if row is None:
            return None
        if row["last_seen_at"] + SESSION_IDLE_SECONDS <= now:
            self.db.execute("DELETE FROM devices WHERE id = ?", (row["id"],))
            return None
        self.db.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
        return dict(row)

    def login(self, account_id: str) -> str:
        return str(
            self.db.execute("SELECT login FROM accounts WHERE id = ?", (account_id,)).fetchone()[0]
        )

    def devices(self, account_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, kind, name, created_at, last_seen_at FROM devices"
            " WHERE account_id = ? ORDER BY created_at",
            (account_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def remove_device(self, account_id: str, identifier: str) -> bool:
        cursor = self.db.execute(
            "DELETE FROM devices WHERE id = ? AND account_id = ?", (identifier, account_id)
        )
        return cursor.rowcount == 1

    def remove_account(self, account_id: str) -> None:
        self.db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def register_mac(self, device_id: str, fields: dict[str, Any]) -> bytes:
        """Record or update the Mac's address and pin; its ticket secret never changes."""
        row = self.db.execute(
            "SELECT ticket_secret FROM macs WHERE device_id = ?", (device_id,)
        ).fetchone()
        secret = bytes(row["ticket_secret"]) if row else secrets.token_bytes(32)
        self.db.execute(
            "INSERT INTO macs"
            " (device_id, host_id, certificate_sha256, tls_name, url, ticket_secret)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (device_id) DO UPDATE SET"
            " host_id = excluded.host_id, certificate_sha256 = excluded.certificate_sha256,"
            " tls_name = excluded.tls_name, url = excluded.url",
            (
                device_id,
                fields["host_id"],
                fields["certificate_sha256"],
                fields["tls_name"],
                fields["url"],
                secret,
            ),
        )
        return secret

    def macs(self, account_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT d.id, d.name, d.last_seen_at, m.host_id, m.certificate_sha256, m.tls_name,"
            " m.url FROM macs m JOIN devices d ON d.id = m.device_id"
            " WHERE d.account_id = ? ORDER BY d.name",
            (account_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def ticket_secret(self, account_id: str, host_id: str) -> bytes | None:
        row = self.db.execute(
            "SELECT m.ticket_secret FROM macs m JOIN devices d ON d.id = m.device_id"
            " WHERE d.account_id = ? AND m.host_id = ?",
            (account_id, host_id),
        ).fetchone()
        return bytes(row["ticket_secret"]) if row else None

    def start_sign_in(self, state: str, challenge: str, *, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        self.db.execute("DELETE FROM sign_ins WHERE expires_at <= ?", (now,))
        self.db.execute(
            "INSERT INTO sign_ins (state, challenge, expires_at) VALUES (?, ?, ?)",
            (state, challenge, now + SIGN_IN_SECONDS),
        )

    def finish_sign_in(self, state: str, account_id: str, *, now: int | None = None) -> str | None:
        """A one-time code for the app to redeem, or None for an unknown or used state."""
        now = int(time.time()) if now is None else now
        code = secrets.token_urlsafe(32)
        cursor = self.db.execute(
            "UPDATE sign_ins SET code_hash = ?, account_id = ?"
            " WHERE state = ? AND code_hash IS NULL AND expires_at > ?",
            (digest(code), account_id, state, now),
        )
        return code if cursor.rowcount == 1 else None

    def redeem_sign_in(self, code: str, verifier: str, *, now: int | None = None) -> str | None:
        """The account behind a one-time code, if the verifier matches; the code is spent."""
        now = int(time.time()) if now is None else now
        row = self.db.execute(
            "DELETE FROM sign_ins WHERE code_hash = ? RETURNING challenge, account_id, expires_at",
            (digest(code),),
        ).fetchone()
        if row is None or row["expires_at"] <= now or row["account_id"] is None:
            return None
        if not secrets.compare_digest(row["challenge"], challenge_for(verifier)):
            return None
        return str(row["account_id"])


def challenge_for(verifier: str) -> str:
    """PKCE S256: base64url(SHA-256(verifier)) without padding."""
    raw = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
