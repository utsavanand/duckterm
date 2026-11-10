"""Private, bounded receipts; a committed queue entry never means agent delivery."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

PENDING = ("queued", "delivering")
TTL_MS = 15 * 60 * 1000
RETENTION_MS = 24 * 60 * 60 * 1000


class MessageError(Exception):
    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        self.reason = reason
        super().__init__(reason)


def receipt(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: row[key]
        for key in ("request_id", "session_id", "status", "reason", "created_at", "updated_at")
    }
    result["expires_at"] = row["created_at"] + TTL_MS
    result["wait_reason"] = (
        ("delivering" if row["status"] == "delivering" else "safe_prompt")
        if row["status"] in PENDING
        else None
    )
    if row["status"] in PENDING:
        result["text"] = row["text"]
    return result


class MessageStore:
    def __init__(self, directory: Path) -> None:
        self.root = directory / "ducky-messages"
        self.path = self.root / "receipts.sqlite"

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self.root.is_symlink():
            raise ValueError("Invalid message directory")
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.stat().st_uid != os.getuid():
            raise ValueError("Invalid message directory owner")
        self.root.chmod(0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("Invalid message journal")
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        db.execute("PRAGMA max_page_count=4096")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS messages (
                device TEXT NOT NULL, request_id TEXT NOT NULL, host TEXT NOT NULL,
                session_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                binding TEXT NOT NULL, pane TEXT NOT NULL, text TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, PRIMARY KEY(device, request_id))"""
            )
            # A fresh server never replays a write whose outcome it cannot know.
            now = int(time.time() * 1000)
            db.execute(
                "UPDATE messages SET status='unknown', reason='server_restarted', "
                "text='', updated_at=? WHERE status='delivering'",
                (now,),
            )
            db.execute(
                "UPDATE messages SET status='cancelled', reason='server_restarted', "
                "text='', updated_at=? WHERE status='queued'",
                (now,),
            )
            self._prune(db, now)

    @staticmethod
    def _prune(db: sqlite3.Connection, now: int) -> None:
        db.execute(
            "UPDATE messages SET status='expired', reason='queue_expired', text='', "
            "updated_at=? WHERE status='queued' AND created_at<?",
            (now, now - TTL_MS),
        )
        db.execute(
            "UPDATE messages SET status='unknown', reason='delivery_uncertain', text='', "
            "updated_at=? WHERE status='delivering' AND updated_at<?",
            (now, now - 60000),
        )
        db.execute(
            "DELETE FROM messages WHERE status NOT IN ('queued','delivering') " "AND updated_at<?",
            (now - RETENTION_MS,),
        )

    def find(self, device: str, request_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM messages WHERE device=? AND request_id=?", (device, request_id)
            ).fetchone()
        return dict(row) if row else None

    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        now = row["created_at"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            existing = db.execute(
                "SELECT * FROM messages WHERE device=? AND request_id=?",
                (row["device"], row["request_id"]),
            ).fetchone()
            if existing:
                if existing["payload_hash"] != row["payload_hash"]:
                    raise MessageError(409, "request_conflict")
                return dict(existing)
            counts = db.execute(
                """SELECT COUNT(*),
                SUM(status IN ('queued','delivering')),
                SUM(device=? AND status IN ('queued','delivering')),
                SUM(session_id=? AND status IN ('queued','delivering')),
                SUM(device=? AND created_at>?) FROM messages""",
                (row["device"], row["session_id"], row["device"], now - 60000),
            ).fetchone()
            if any(
                (value or 0) >= limit
                for value, limit in zip(counts, (2000, 100, 20, 5, 30), strict=True)
            ):
                raise MessageError(429, "queue_limit")
            keys = list(row)
            db.execute(
                f"INSERT INTO messages ({','.join(keys)}) VALUES "
                f"({','.join('?' for _ in keys)})",
                tuple(row.values()),
            )
        return row

    def pending(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            self._prune(db, int(time.time() * 1000))
            rows = db.execute(
                "SELECT * FROM messages WHERE status='queued' "
                "ORDER BY created_at, rowid LIMIT 100"
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_for_session(self, session: str, now: int) -> list[dict[str, Any]]:
        """An observation only: never create, recover, prune or advance the queue."""
        try:
            root, file = self.root.lstat(), self.path.lstat()
        except FileNotFoundError:
            return []
        if (
            not stat.S_ISDIR(root.st_mode)
            or not stat.S_ISREG(file.st_mode)
            or root.st_uid != os.getuid()
            or file.st_uid != os.getuid()
        ):
            raise ValueError("Invalid message journal")
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute(
                "SELECT * FROM messages WHERE session_id=? "
                "AND status IN ('queued','delivering') AND created_at>? "
                "AND (status!='delivering' OR updated_at>=?) "
                "ORDER BY created_at, rowid LIMIT 5",
                (session, now - TTL_MS, now - 60000),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            db.close()

    def transition(self, row: dict[str, Any], status: str, reason: str | None = None) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if status == "delivering":
                self._prune(db, int(time.time() * 1000))
            result = db.execute(
                "UPDATE messages SET status=?,reason=?,updated_at=?, "
                "text=CASE WHEN ? IN ('queued','delivering') THEN text ELSE '' END "
                "WHERE device=? AND request_id=? AND status=?",
                (
                    status,
                    reason,
                    int(time.time() * 1000),
                    status,
                    row["device"],
                    row["request_id"],
                    row["status"],
                ),
            )
        return result.rowcount == 1

    def for_session(self, device: str, session: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE device=? AND session_id=? "
                "ORDER BY created_at DESC LIMIT 30",
                (device, session),
            ).fetchall()
        return [receipt(dict(row)) for row in reversed(rows)]

    def cancel(self, reason: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE messages SET status='cancelled',reason=?,text='',updated_at=? "
                "WHERE status='queued'",
                (reason, int(time.time() * 1000)),
            )


def payload_hash(request: dict[str, str]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
