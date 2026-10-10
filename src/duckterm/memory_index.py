"""Private, disposable FTS5 cache. All source authorization is outside this module."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from duckterm.core.session_api import APIError

CHUNK_CHARS = 4000
SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id TEXT NOT NULL, version TEXT NOT NULL, PRIMARY KEY(id, version)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    source_id UNINDEXED, version UNINDEXED, record_id UNINDEXED,
    offset UNINDEXED, title, body, tokenize='porter unicode61'
);
"""


def cache_version(source: dict[str, Any]) -> str:
    """Reader changes invalidate derived chunks without changing raw source handles."""
    version = str(source["version"])
    return version + ":" + source["normalization"] if source.get("normalization") else version


def query(path: Path, sources: list[dict[str, Any]], text: str, limit: int) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise APIError(409, "Memory index must not be a symbolic link")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        # Old disposable indexes used one version per source. Rebuild once.
        columns = list(conn.execute("PRAGMA table_info(versions)"))
        if columns and columns[1][5] == 0:
            conn.executescript("DROP TABLE versions; DROP TABLE chunks;")
        conn.executescript(SCHEMA)
        current = {(s["id"], cache_version(s)) for s in sources}
        with conn:
            for row in conn.execute("SELECT id,version FROM versions").fetchall():
                if (row["id"], row["version"]) not in current:
                    conn.execute(
                        "DELETE FROM chunks WHERE source_id=? AND version=?",
                        (row["id"], row["version"].split(":", 1)[0]),
                    )
                    conn.execute(
                        "DELETE FROM versions WHERE id=? AND version=?", (row["id"], row["version"])
                    )
            for source in sources:
                cached = cache_version(source)
                if source["kind"] in {"revision", "checkpoint"}:
                    conn.execute(
                        "DELETE FROM chunks WHERE source_id=? AND version=?",
                        (source["id"], source["version"]),
                    )
                    conn.execute(
                        "DELETE FROM versions WHERE id=? AND version=?",
                        (source["id"], cached),
                    )
                if conn.execute(
                    "SELECT 1 FROM versions WHERE id=? AND version=?",
                    (source["id"], cached),
                ).fetchone():
                    continue
                for record in source["records"]:
                    body = record["text"]
                    for offset in range(0, len(body), CHUNK_CHARS - 200):
                        conn.execute(
                            "INSERT INTO chunks VALUES (?,?,?,?,?,?)",
                            (
                                source["id"],
                                source["version"],
                                record["id"],
                                offset,
                                source["title"],
                                body[offset : offset + CHUNK_CHARS],
                            ),
                        )
                conn.execute("INSERT INTO versions VALUES (?,?)", (source["id"], cached))
        # Natural-language queries remain keyword searches. No raw FTS expression
        # is accepted, and OR avoids requiring stop words from a human's question.
        words = re.findall(r"[^\W_]+", text, re.UNICODE)
        stop = {"the", "a", "an", "why", "did", "we", "do", "is", "was", "how", "what", "to", "of"}
        words = [w for w in words if w.lower() not in stop][:16]
        if not words:
            return []
        expression = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
        return [
            dict(row)
            for row in conn.execute(
                "SELECT source_id,version,record_id,offset,title,"
                "snippet(chunks,5,'','',' … ',48) AS excerpt FROM chunks "
                "WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                (expression, limit),
            )
        ]
    finally:
        conn.close()
