"""Private bounded analysis journal, separate from agent messages and desktop chats."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

RETENTION_MS = 24 * 60 * 60 * 1000
QUEUE_MS = 2 * 60 * 1000
WORK_MS = 4 * 60 * 1000
PENDING = ("queued", "running")


class AnalysisError(Exception):
    def __init__(self, status: int, reason: str) -> None:
        self.status, self.reason = status, reason
        super().__init__(reason)


def receipt(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "request_id",
        "context_id",
        "status",
        "reason",
        "question",
        "answer",
        "created_at",
        "updated_at",
        "expires_at",
        "captured_at",
        "source_count",
    )
    return {
        **{key: row[key] for key in keys},
        "scope": json.loads(row["scope"]),
        "truncated": bool(row["truncated"]),
    }


class AnalysisStore:
    def __init__(self, directory: Path) -> None:
        self.root = directory / "ducky-analysis"
        self.path = self.root / "receipts.sqlite"

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self.root.is_symlink():
            raise ValueError("Invalid analysis directory")
        self.root.mkdir(mode=0o700, exist_ok=True)
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Invalid analysis directory")
        self.root.chmod(0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("Invalid analysis journal")
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        db = sqlite3.connect(self.path, timeout=0.1)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA max_page_count=2048")
            with db:
                yield db
        finally:
            db.close()

    def initialize(self, now: int) -> None:
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS questions (
                host TEXT NOT NULL, device TEXT NOT NULL, request_id TEXT NOT NULL,
                context_id TEXT NOT NULL, scope TEXT NOT NULL, binding TEXT NOT NULL,
                configuration TEXT NOT NULL, revision INTEGER NOT NULL, payload_hash TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT, question TEXT NOT NULL, answer TEXT,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                captured_at INTEGER, source_count INTEGER NOT NULL, truncated INTEGER NOT NULL,
                PRIMARY KEY(host,device,request_id))"""
            )
            db.execute(
                "UPDATE questions SET status='interrupted',reason='server_restarted', "
                "answer=NULL,updated_at=MAX(updated_at,created_at,?) "
                "WHERE status IN ('queued','running')",
                (now,),
            )
            self._prune(db, now)

    @staticmethod
    def _prune(db: sqlite3.Connection, now: int) -> None:
        db.execute("DELETE FROM questions WHERE created_at<=?", (now - RETENTION_MS,))
        db.execute(
            "UPDATE questions SET status='expired',reason='queue_expired', "
            "updated_at=MAX(updated_at,created_at,?) "
            "WHERE status='queued' AND created_at<=?",
            (now, now - QUEUE_MS),
        )
        db.execute(
            "UPDATE questions SET status='expired',reason='work_expired', "
            "updated_at=MAX(updated_at,created_at,?) "
            "WHERE status='running' AND expires_at<=?",
            (now, now),
        )
        # Keep small idempotency/rate records even when the answer count is full.
        db.execute(
            "UPDATE questions SET status='expired',reason='history_pruned',answer=NULL "
            "WHERE rowid IN (SELECT rowid FROM questions WHERE status='complete' "
            "ORDER BY updated_at DESC,rowid DESC LIMIT -1 OFFSET 100)"
        )

    def sweep(self, now: int) -> None:
        with self.connect() as db:
            self._prune(db, now)

    def find(self, host: str, device: str, request_id: str, now: int) -> dict[str, Any] | None:
        with self.connect() as db:
            self._prune(db, now)
            row = db.execute(
                "SELECT * FROM questions WHERE host=? AND device=? AND request_id=?",
                (host, device, request_id),
            ).fetchone()
            return dict(row) if row else None

    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        now = row["created_at"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            old = db.execute(
                "SELECT * FROM questions WHERE host=? AND device=? AND request_id=?",
                (row["host"], row["device"], row["request_id"]),
            ).fetchone()
            if old:
                if old["payload_hash"] != row["payload_hash"]:
                    raise AnalysisError(409, "request_conflict")
                return dict(old)
            counts = db.execute(
                """SELECT COUNT(*), SUM(status='queued'),
                SUM(host=? AND device=? AND status IN ('queued','running')),
                SUM(host=? AND device=? AND created_at>?),
                SUM(host=? AND device=?), SUM(host=?) FROM questions""",
                (
                    row["host"],
                    row["device"],
                    row["host"],
                    row["device"],
                    now - 60000,
                    row["host"],
                    row["device"],
                    row["host"],
                ),
            ).fetchone()
            total, queued, active, minute, device_day, host_day = (value or 0 for value in counts)
            if total >= 1000:
                raise AnalysisError(503, "storage_unavailable")
            if queued >= 8 or active:
                raise AnalysisError(429, "queue_limit")
            if minute >= 4 or device_day >= 40 or host_day >= 100:
                raise AnalysisError(429, "rate_limit")
            keys = list(row)
            db.execute(
                f"INSERT INTO questions ({','.join(keys)}) VALUES "
                f"({','.join('?' for _ in keys)})",
                tuple(row.values()),
            )
        return row

    def queued(self, now: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            self._prune(db, now)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM questions WHERE status='queued' "
                    "ORDER BY created_at,rowid LIMIT 8"
                )
            ]

    def transition(
        self,
        row: dict[str, Any],
        status: str,
        now: int,
        *,
        reason: str | None = None,
        answer: str | None = None,
        captured_at: int | None = None,
        source_count: int = 0,
        truncated: bool = False,
    ) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            changed = db.execute(
                """UPDATE questions SET status=?,reason=?,answer=?,
                updated_at=MAX(updated_at,created_at,?),
                captured_at=COALESCE(?,captured_at), source_count=?,truncated=?
                WHERE host=? AND device=? AND request_id=? AND status=?""",
                (
                    status,
                    reason,
                    answer,
                    max(now, captured_at or 0),
                    captured_at,
                    source_count,
                    int(truncated),
                    row["host"],
                    row["device"],
                    row["request_id"],
                    row["status"],
                ),
            )
            self._prune(db, now)
            return changed.rowcount == 1

    def history(self, row: dict[str, Any], now: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            self._prune(db, now)
            rows = db.execute(
                """SELECT * FROM questions WHERE host=? AND device=? AND scope=?
                AND binding=? AND configuration=? AND revision=? AND status='complete'
                ORDER BY created_at DESC,rowid DESC LIMIT 20""",
                tuple(
                    row[key]
                    for key in ("host", "device", "scope", "binding", "configuration", "revision")
                ),
            )
            result: list[dict[str, Any]] = []
            size = 0
            for saved in rows:
                value = receipt(dict(saved))
                size += len(json.dumps(value, ensure_ascii=False).encode())
                if size > 1024 * 1024:
                    break
                result.append(value)
            return list(reversed(result))

    def pending(self, row: dict[str, Any], now: int) -> dict[str, Any] | None:
        with self.connect() as db:
            self._prune(db, now)
            saved = db.execute(
                """SELECT * FROM questions WHERE host=? AND device=? AND scope=?
                AND binding=? AND configuration=? AND revision=? AND status IN ('queued','running')
                ORDER BY created_at DESC LIMIT 1""",
                tuple(
                    row[key]
                    for key in ("host", "device", "scope", "binding", "configuration", "revision")
                ),
            ).fetchone()
            return receipt(dict(saved)) if saved else None

    def invalidate(self, now: int, *, reason: str, completed: bool) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE questions SET status='cancelled',reason=?,answer=NULL, "
                "updated_at=MAX(updated_at,created_at,?) "
                + ("" if completed else "WHERE status IN ('queued','running')"),
                (reason, now),
            )
