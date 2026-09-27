"""Local, session-owned snapshots of explicitly registered agent deliverables."""

import base64
import binascii
import hashlib
import os
import sqlite3
import stat
import time
import uuid
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_SESSION_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_SESSION_FILES = 200
MEDIA_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".csv": "text/plain",
    ".json": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    title TEXT NOT NULL,
    source_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    content BLOB NOT NULL,
    UNIQUE(session_key, source_path)
);
CREATE INDEX IF NOT EXISTS artifacts_session ON artifacts(session_key, updated_at);
"""
FIELDS = "id, session_key, title, source_path, media_type, size, sha256, created_at, updated_at"


class ArtifactError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ArtifactError(400, f"{field} must be nonempty text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ArtifactError(400, f"{field} must be valid Unicode") from exc
    if size > limit:
        raise ArtifactError(400, f"{field} is too long")
    return value


def registration(path: Path, title: str | None = None) -> dict[str, Any]:
    """Read on the agent's host, never let a remote request read server files."""
    source = path.expanduser().absolute()
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactError(400, "Artifact must be a regular file")
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ArtifactError(413, "Artifact exceeds the 5 MiB file limit")
    return {
        "source_path": str(source),
        "title": title if title is not None else source.name,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


class ArtifactStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.executescript(SCHEMA)

    def list(self, session_key: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                f"SELECT {FIELDS} FROM artifacts WHERE session_key = ? "
                "ORDER BY updated_at DESC, id",
                (session_key,),
            )
        ]

    def get(self, session_key: str, artifact_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            f"SELECT {FIELDS}, content FROM artifacts WHERE session_key = ? AND id = ?",
            (session_key, artifact_id),
        ).fetchone()
        if row is None:
            raise ArtifactError(404, "Artifact not found")
        result = dict(row)
        result["content_base64"] = base64.b64encode(result.pop("content")).decode("ascii")
        return result

    def remove(self, session_key: str, artifact_id: str) -> None:
        self.conn.execute(
            "DELETE FROM artifacts WHERE session_key = ? AND id = ?", (session_key, artifact_id)
        )
        self.conn.commit()

    def register(self, session_key: str, req: dict[str, Any]) -> dict[str, Any]:
        if set(req) != {"title", "source_path", "content_base64"}:
            raise ArtifactError(400, "Expected title, source_path and content_base64")
        title = _text(req["title"], "title", 512)
        source = _text(req["source_path"], "source_path", 4096)
        if not Path(source).is_absolute() or ".." in Path(source).parts:
            raise ArtifactError(400, "source_path must be an absolute path without traversal")
        source = str(Path(source))
        encoded = req["content_base64"]
        if not isinstance(encoded, str) or len(encoded) > ((MAX_FILE_BYTES + 2) // 3) * 4:
            raise ArtifactError(413, "Artifact exceeds the 5 MiB file limit")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ArtifactError(400, "Invalid base64 artifact content") from exc
        if len(content) > MAX_FILE_BYTES:
            raise ArtifactError(413, "Artifact exceeds the 5 MiB file limit")
        media_type = MEDIA_TYPES.get(Path(source).suffix.lower(), "application/octet-stream")
        if media_type.startswith("text/") or media_type == "image/svg+xml":
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ArtifactError(400, "Text artifacts must be UTF-8") from exc
        digest = hashlib.sha256(content).hexdigest()
        # One transaction keeps replacement and quotas consistent across clients.
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            if (
                self.conn.execute(
                    "SELECT 1 FROM sessions WHERE session_key = ?", (session_key,)
                ).fetchone()
                is None
            ):
                raise ArtifactError(404, "Session not found")
            old = self.conn.execute(
                f"SELECT {FIELDS} FROM artifacts WHERE session_key = ? AND source_path = ?",
                (session_key, source),
            ).fetchone()
            if old and old["sha256"] == digest and old["title"] == title:
                return dict(old)
            count, used = self.conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM artifacts WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            total = self.conn.execute("SELECT COALESCE(SUM(size), 0) FROM artifacts").fetchone()[0]
            delta = len(content) - (old["size"] if old else 0)
            if (not old and count >= MAX_SESSION_FILES) or used + delta > MAX_SESSION_BYTES:
                raise ArtifactError(413, "Session artifact limit reached; remove saved artifacts")
            if total + delta > MAX_TOTAL_BYTES:
                raise ArtifactError(413, "Artifact storage limit reached; remove saved artifacts")
            now = time.time_ns() // 1_000_000
            artifact_id = old["id"] if old else uuid.uuid4().hex
            created_at = old["created_at"] if old else now
            self.conn.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_key, source_path) DO UPDATE SET title=excluded.title, "
                "media_type=excluded.media_type, size=excluded.size, sha256=excluded.sha256, "
                "updated_at=excluded.updated_at, content=excluded.content",
                (
                    artifact_id,
                    session_key,
                    title,
                    source,
                    media_type,
                    len(content),
                    digest,
                    created_at,
                    now,
                    content,
                ),
            )
        return next(row for row in self.list(session_key) if row["id"] == artifact_id)
