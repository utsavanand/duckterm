"""Durable digest history: every validated deliverable / learning / next
action is a row that accumulates across a session's life, instead of a JSON
blob the next regeneration overwrites.

Lifecycle: the progress pipeline generates a candidate digest, validates it
(core/progress: L1 sense-check + L2 compare-against-stored, one summarizer
call), then merge() applies the verdicts here — new items insert, duplicates
are dropped (a normalized-text guard enforces this in code regardless of what
the validator said), and next_actions the validator marked completed flip to
'done' rather than disappearing.

The server injects HistoryStore's connection so item updates, immutable summary
revisions and compatibility caches commit together. Standalone callers own their
connection and must pass the database-version check before writing.
"""

import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from duckterm.helpers import paths
from duckterm.persistence.saved_state import REVISION_BUCKET, SCHEMA_VERSION, revision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS digest_items (
    id          TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    bucket      TEXT NOT NULL,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_digest_items_session
    ON digest_items(session_key, bucket, created_at);
"""

BUCKETS = ("deliverables", "learnings", "user_learnings", "next_actions")


def _normalize(text: str) -> str:
    """Dedup key: case/punctuation/whitespace-insensitive."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class DigestStore:
    def __init__(
        self, db_path: Path | None = None, *, conn: sqlite3.Connection | None = None
    ) -> None:
        self._owns_connection = conn is None
        if conn is None:
            path = db_path if db_path is not None else paths.db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        if int(conn.execute("PRAGMA user_version").fetchone()[0]) > SCHEMA_VERSION:
            raise RuntimeError("digest database is newer than this DuckTerm; upgrade first")
        if conn.in_transaction:
            raise RuntimeError("initialize DigestStore outside a transaction")
        conn.executescript(_SCHEMA)

    def revision(self, session_key: str, identity: str | None) -> dict[str, Any] | None:
        return revision(self._conn, session_key, identity)

    def find_revision(self, session_key: str, input_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id FROM digest_items WHERE session_key=? AND bucket=? "
            "AND CASE WHEN json_valid(text) THEN json_extract(text, '$.input_key') "
            "END=? ORDER BY rowid DESC LIMIT 1",
            (session_key, REVISION_BUCKET, input_key),
        ).fetchone()
        return self.revision(session_key, row[0]) if row else None

    def add_revision(self, session_key: str, value: dict[str, Any], now: int) -> str:
        if not self._conn.in_transaction:
            raise RuntimeError("summary revisions require the shared write transaction")
        identity = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO digest_items VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (
                identity,
                session_key,
                REVISION_BUCKET,
                json.dumps({**value, "format": REVISION_BUCKET}),
                now,
                now,
            ),
        )
        return identity

    def items(self, session_key: str) -> list[dict[str, Any]]:
        """All digest items for a session, oldest first within each bucket."""
        rows = self._conn.execute(
            "SELECT id, bucket, text, status, created_at, updated_at "
            "FROM digest_items WHERE session_key = ? "
            "AND bucket IN ('deliverables','learnings','user_learnings','next_actions') "
            "ORDER BY created_at",
            (session_key,),
        ).fetchall()
        return [dict(r) for r in rows]

    def merge(
        self,
        session_key: str,
        accepted: list[dict[str, str]],
        done_ids: list[str],
        now: int,
        *,
        commit: bool = True,
    ) -> dict[str, int]:
        """Apply validated verdicts: insert accepted items (unknown buckets and
        normalized-text duplicates are dropped here regardless of what the
        validator claimed), flip listed next_actions to done. Returns counts."""
        existing = {
            (r["bucket"], _normalize(r["text"]))
            for r in self._conn.execute(
                "SELECT bucket, text FROM digest_items WHERE session_key = ?",
                (session_key,),
            ).fetchall()
        }
        inserted = 0
        for item in accepted:
            bucket, text = str(item.get("bucket", "")), str(item.get("text", "")).strip()
            key = (bucket, _normalize(text))
            if bucket not in BUCKETS or not text or key in existing:
                continue
            existing.add(key)
            self._conn.execute(
                "INSERT INTO digest_items (id, session_key, bucket, text, status,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (uuid.uuid4().hex, session_key, bucket, text, now, now),
            )
            inserted += 1
        done = 0
        for item_id in done_ids:
            cur = self._conn.execute(
                "UPDATE digest_items SET status = 'done', updated_at = ? "
                "WHERE id = ? AND session_key = ? AND bucket = 'next_actions'",
                (now, str(item_id), session_key),
            )
            done += cur.rowcount
        if commit:
            self._conn.commit()
        return {"inserted": inserted, "done": done}

    def delete_session(self, session_key: str) -> None:
        self._conn.execute("DELETE FROM digest_items WHERE session_key = ?", (session_key,))
        self._conn.commit()

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()
