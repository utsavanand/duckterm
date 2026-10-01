"""Durable, credential-scoped discovery and cooperative session inboxes.

This broker never writes to a terminal. Receiving agents explicitly read their
inbox and submit correlated answers; supported hooks surface task-end notices.
"""

import hashlib
import json
import re
import secrets
import sqlite3
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from duckterm.helpers import session_credentials
from duckterm.persistence import mail_analytics
from duckterm.persistence.artifacts import MAX_REQUEST_BYTES as MAX_ARTIFACT_REQUEST_BYTES
from duckterm.persistence.artifacts import ArtifactError, ArtifactStore
from duckterm.runtimes.base import AT_REST_STATES

# Store a far-future deadline so older servers do not immediately expire
# persistent rows. The public API represents this sentinel as zero.
NO_DEADLINE = 253402300799000  # 9999-12-31 UTC

MAX_BODY_BYTES = 2 * 1024 * 1024  # Allows JSON escapes for a full 256 KiB answer.

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_api_members (
    session_key TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    root TEXT NOT NULL,
    folder TEXT NOT NULL,
    root_mode TEXT NOT NULL DEFAULT 'explicit',
    api_name TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    activity TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL,
    UNIQUE(root, api_name)
);
CREATE TABLE IF NOT EXISTS session_questions (
    id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    root TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    answer TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    answered_at INTEGER,
    idempotency_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'question',
    UNIQUE(sender, idempotency_key)
);
CREATE TABLE IF NOT EXISTS session_broadcasts (
    request_key TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_inbox_delivery (
    question_id TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at INTEGER NOT NULL DEFAULT 0,
    last_read_at INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS questions_recipient ON session_questions(recipient, created_at);
CREATE INDEX IF NOT EXISTS questions_sender ON session_questions(sender, created_at);
"""


class APIError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def _text(value: Any, field: str, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise APIError(400, f'{field} must be a {"nonempty " if not empty else ""}string')
    try:
        length = len(value.encode())
    except UnicodeEncodeError as exc:
        raise APIError(400, f"{field} must contain valid Unicode") from exc
    if length > limit:
        raise APIError(413, f"{field} exceeds {limit} bytes")
    return value


def _inside(folder: str, root: str) -> bool:
    return folder == root or folder.startswith(root + "/")


def _check_merge_key(
    conn: sqlite3.Connection, request_key: str, merged_from: object, key: str, priority: bool
) -> None:
    """A merge summary names its child, which must be another existing
    session, and its key must be merge:<child>:<unique>; no other message
    may use the merge: prefix, so the inbox's merge origin can be trusted."""
    if merged_from is None:
        if request_key.startswith(MERGE_PREFIX):
            raise APIError(400, "a merge: request key needs merged_from")
        return
    if not priority:
        raise APIError(400, "a merge summary is a priority message")
    if not isinstance(merged_from, str) or merged_from == key or ":" in merged_from:
        raise APIError(400, "merged_from must be another session's key")
    if (
        conn.execute("SELECT 1 FROM sessions WHERE session_key = ?", (merged_from,)).fetchone()
        is None
    ):
        raise APIError(404, "merged_from session not found")
    prefix = f"{MERGE_PREFIX}{merged_from}:"
    if not request_key.startswith(prefix) or len(request_key) == len(prefix):
        raise APIError(400, f"request_key must be {prefix}<unique>")


def _public_question(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "sender",
        "recipient",
        "sender_name",
        "question",
        "status",
        "answer",
        "created_at",
        "expires_at",
        "answered_at",
        "kind",
    )
    result = {field: row[field] for field in fields}
    result["sender_kind"] = "owner" if row["sender"] == "owner" else "session"
    result["priority"] = bool(row.get("priority"))
    # A priority owner message is acknowledged by replying.
    result["requires_reply"] = row["kind"] != "broadcast" or result["priority"]
    if result["expires_at"] == NO_DEADLINE:
        result["expires_at"] = 0
    if row["sender"] == "owner" and row["kind"] == "broadcast" and result["priority"]:
        # The key GET/DELETE /broadcasts/:request_key take, without the
        # recipient suffix each copy carries.
        request_key = row["idempotency_key"].removesuffix(":" + row["recipient"])
        result["request_key"] = request_key
        if request_key.startswith(MERGE_PREFIX):
            # owner_message validated the child when it stored this key.
            result["origin"] = {"kind": "merge", "from_session": request_key.split(":")[1]}
    return result


# Request keys of fork merge summaries: merge:<child session key>:<unique>.
# Only owner_message with a validated merged_from may store one.
MERGE_PREFIX = "merge:"

# A priority owner message nobody has replied to: never swept while open.
_OPEN_PRIORITY = "(priority = 1 AND status IN ('queued', 'read'))"
PRIORITY_SNIPPET = 400


class SessionAPI:
    def __init__(self, conn: sqlite3.Connection, credential_dir: Path) -> None:
        self.before_retire: Callable[[], None] = lambda: None
        self.conn = conn
        self.credential_dir = credential_dir
        self.artifacts = ArtifactStore(conn)
        conn.executescript(SCHEMA)
        question_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(session_questions)")
        }
        if "kind" not in question_columns:
            conn.execute(
                "ALTER TABLE session_questions ADD COLUMN kind TEXT NOT NULL DEFAULT 'question'"
            )
        if "priority" not in question_columns:
            # Owner broadcasts only; a peer can never set it (see broadcast()).
            conn.execute(
                "ALTER TABLE session_questions ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"
            )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(session_api_members)")}
        if "root_mode" not in columns:
            conn.execute(
                "ALTER TABLE session_api_members "
                "ADD COLUMN root_mode TEXT NOT NULL DEFAULT 'explicit'"
            )

    def _save_token(self, key: str, token: str) -> None:
        session_credentials.write_private(
            session_credentials.credential_path(key, self.credential_dir),
            {"session_id": key, "token": token},
        )

    def set_url(self, url: str) -> None:
        session_credentials.write_private(self.credential_dir / "server.json", {"url": url})

    def backfill(self) -> int:
        """Idempotent startup migration, also repairing a missing capability file."""
        count = 0
        for row in self.conn.execute("SELECT session_key, state FROM sessions").fetchall():
            if row["state"] not in AT_REST_STATES:
                count += self.ensure(row["session_key"])
        self.conn.commit()
        return count

    def ensure(self, key: str) -> int:
        session = self._session(key)
        row = self.conn.execute(
            "SELECT * FROM session_api_members WHERE session_key = ?", (key,)
        ).fetchone()
        created = row is None
        if row is None:
            folder = str(session.get("grp") or "")
            self.conn.execute(
                "INSERT INTO session_api_members "
                "(session_key, token_hash, root, folder, api_name, updated_at, root_mode) "
                "VALUES (?, ?, ?, ?, ?, ?, 'automatic')",
                (
                    key,
                    secrets.token_hex(32),
                    folder.split("/")[0],
                    folder,
                    self._available_alias(key, folder.split("/")[0], key),
                    int(time.time() * 1000),
                ),
            )
            row = self.conn.execute(
                "SELECT * FROM session_api_members WHERE session_key = ?", (key,)
            ).fetchone()
        assert row is not None
        path = session_credentials.credential_path(key, self.credential_dir)
        try:
            token = json.loads(path.read_text())["token"]
            valid = isinstance(token, str) and secrets.compare_digest(
                hashlib.sha256(token.encode()).hexdigest(), row["token_hash"]
            )
        except (OSError, ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            token = secrets.token_urlsafe(32)
            self._save_token(key, token)
            self.conn.execute(
                "UPDATE session_api_members SET token_hash = ? WHERE session_key = ?",
                (hashlib.sha256(token.encode()).hexdigest(), key),
            )
        return int(created)

    def _available_alias(self, alias: str, root: str, key: str) -> str:
        conflict = self.conn.execute(
            "SELECT 1 FROM session_api_members "
            "WHERE root = ? AND api_name = ? AND session_key != ?",
            (root, alias, key),
        ).fetchone()
        if conflict:
            return alias[:80] + "-" + hashlib.sha256(key.encode()).hexdigest()[:32]
        return alias

    def sync_memberships(self, *, moved: tuple[str, str] | None = None) -> None:
        """Keep capabilities while membership follows owner-directed folder changes."""
        rows = self.conn.execute(
            "SELECT m.*, COALESCE(s.grp, '') AS current_folder FROM session_api_members m "
            "JOIN sessions s ON s.session_key = m.session_key"
        ).fetchall()
        now = int(time.time() * 1000)
        roots: dict[str, tuple[str, str]] = {}
        for row in rows:
            folder = row["current_folder"]
            root = row["root"]
            if moved and _inside(root, moved[0]):
                root = moved[1] + root[len(moved[0]) :]
            mode = row["root_mode"]
            if mode == "automatic" or not root or not _inside(folder, root):
                root = folder.split("/")[0]
                mode = "automatic"
            roots[row["session_key"]] = (row["root"], root)
            self.conn.execute(
                "UPDATE session_api_members SET folder = ?, root = ?, api_name = ?, "
                "updated_at = ?, root_mode = ? "
                "WHERE session_key = ?",
                (
                    folder,
                    root,
                    self._available_alias(row["api_name"], root, row["session_key"]),
                    now,
                    mode,
                    row["session_key"],
                ),
            )
        # Preserve a thread only when both of its original authorized participants
        # still move together into the same scope. Moving just one never leaks it.
        for question in self.conn.execute(
            "SELECT id, sender, recipient, root, kind FROM session_questions"
        ).fetchall():
            target = roots.get(question["recipient"])
            if question["sender"] == "owner":
                if moved and target and _inside(question["root"], moved[0]):
                    self.conn.execute(
                        "UPDATE session_questions SET root = ? WHERE id = ?",
                        (target[1], question["id"]),
                    )
                continue
            source = roots.get(question["sender"])
            if (
                source
                and target
                and source[0] == target[0] == question["root"]
                and source[1]
                and source[1] == target[1]
            ):
                self.conn.execute(
                    "UPDATE session_questions SET root = ? WHERE id = ?",
                    (source[1], question["id"]),
                )
        self.conn.execute(
            "UPDATE session_questions SET status = 'cancelled', answered_at = ? "
            "WHERE kind = 'question' AND sender != 'owner' "
            "AND status IN ('queued', 'accepted') "
            "AND NOT EXISTS (SELECT 1 FROM session_api_members a JOIN session_api_members b "
            "ON a.root = b.root WHERE a.session_key = sender AND b.session_key = recipient "
            "AND a.root = session_questions.root AND a.root != '')",
            (int(time.time() * 1000),),
        )

        self.conn.execute(
            "UPDATE session_questions SET status = 'cancelled', answered_at = ? "
            "WHERE sender = 'owner' AND status IN ('queued', 'accepted') AND NOT EXISTS "
            "(SELECT 1 FROM session_api_members m WHERE m.session_key = recipient "
            "AND m.root = session_questions.root AND m.root != '')",
            (now,),
        )

    def broadcast_targets(self, folder: str) -> list[dict[str, Any]]:
        """Owner preview: include stopped/busy sessions; enrollment controls delivery."""
        prefix = folder + "/"
        rows = self.conn.execute(
            "SELECT s.session_key, s.name, s.grp, s.state, m.root "
            "FROM sessions s LEFT JOIN session_api_members m ON m.session_key = s.session_key "
            "WHERE s.grp = ? OR substr(s.grp, 1, ?) = ? ORDER BY s.session_key",
            (folder, len(prefix), prefix),
        ).fetchall()
        targets = []
        for row in rows:
            eligible = bool(row["root"] and _inside(row["grp"], row["root"]))
            targets.append(
                {
                    "session_id": row["session_key"],
                    "name": row["name"] or row["session_key"],
                    "state": row["state"],
                    "eligible": eligible,
                    "reason": None if eligible else "not enrolled in a shared folder",
                }
            )
        return targets

    def broadcast(self, folder: str, req: dict[str, Any]) -> dict[str, Any]:
        """An owner broadcast (owner routes only). priority=True pins it: idle
        agents are reminded at once, busy ones at their next turn end, again
        at every turn end until they reply, and it isn't swept while open."""
        if set(req) - {"text", "request_key", "priority"}:
            raise APIError(400, "unknown broadcast fields")
        if not isinstance(req.get("priority", False), bool):
            raise APIError(400, "priority must be true or false")
        priority = int(req.get("priority", False))
        message = _text(req.get("text"), "text", 16384)
        request_key = _text(req.get("request_key", secrets.token_hex(16)), "request_key", 128)
        if request_key.startswith(MERGE_PREFIX):
            raise APIError(400, "merge: request keys are for fork merge summaries")
        # Plain broadcasts keep the pre-priority hash, so a retry of one sent
        # by an older server still matches its stored request_key.
        hashed = [folder, message, 1] if priority else [folder, message]
        digest = hashlib.sha256(json.dumps(hashed).encode()).hexdigest()
        self._sweep()
        old = self.conn.execute(
            "SELECT content_hash, result FROM session_broadcasts WHERE request_key = ?",
            (request_key,),
        ).fetchone()
        if old:
            if old["content_hash"] != digest:
                raise APIError(409, "request_key already used for different content")
            return json.loads(old["result"])  # type: ignore[no-any-return]
        now = int(time.time() * 1000)
        results = []
        with self.conn:
            for target in self.broadcast_targets(folder):
                result = dict(target)
                result["status"] = "queued" if target["eligible"] else "skipped"
                if target["eligible"]:
                    key = target["session_id"]
                    member = self._member(key, live=False)
                    notice_id = "b-" + secrets.token_hex(16)
                    self.conn.execute(
                        "INSERT INTO session_questions "
                        "(id, sender, recipient, sender_name, root, question, created_at, "
                        "expires_at, idempotency_key, content_hash, kind, priority) "
                        "VALUES (?, 'owner', ?, 'You', ?, ?, ?, ?, ?, ?, 'broadcast', ?)",
                        (
                            notice_id,
                            key,
                            member["root"],
                            message,
                            now,
                            NO_DEADLINE,
                            request_key + ":" + key,
                            digest,
                            priority,
                        ),
                    )
                    result["message_id"] = notice_id
                results.append(result)
            response = {
                "folder": folder,
                "request_key": request_key,
                "priority": bool(priority),
                "results": results,
                "queued": sum(r["status"] == "queued" for r in results),
                "skipped": sum(r["status"] == "skipped" for r in results),
            }
            self.conn.execute(
                "INSERT INTO session_broadcasts VALUES (?, ?, ?, ?)",
                (request_key, digest, now, json.dumps(response)),
            )
        return response

    def owner_message(
        self,
        key: str,
        text: object,
        *,
        request_key: str | None = None,
        priority: bool = False,
        merged_from: object = None,
        question: bool = False,
    ) -> str:
        """One owner notice to one session, the same record a folder broadcast
        queues, keyed like a broadcast copy when request_key is given. Returns
        the message id. priority=True makes it a one-recipient priority
        broadcast: the same pin, Oracle reminder, status and cancel
        (GET/DELETE /broadcasts/:request_key). Fork merge-back delivers its
        summary this way. question=True (folder chat) asks for an answer
        instead, under request_key as given."""
        message = _text(text, "text", 16384)
        row = self.conn.execute(
            "SELECT s.grp, m.root FROM sessions s "
            "LEFT JOIN session_api_members m ON m.session_key = s.session_key "
            "WHERE s.session_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise APIError(404, "session not found")
        if not (row["root"] and _inside(row["grp"] or "", row["root"])):
            raise APIError(
                409,
                "This session has no inbox because it isn't in a shared folder. "
                "Type into its prompt or open its terminal instead.",
            )
        digest = hashlib.sha256(
            json.dumps([key, message, 1] if priority else [key, message]).encode()
        ).hexdigest()
        if priority and request_key is None:
            raise APIError(400, "a priority message needs a request_key")
        if merged_from is not None and not priority:
            raise APIError(400, "a merge summary is a priority message")
        if question and (priority or request_key is None):
            raise APIError(400, "an owner question needs a request_key and no priority")
        if request_key is not None:
            request_key = _text(request_key, "request_key", 128)
            _check_merge_key(self.conn, request_key, merged_from, key, priority)
            if not question:
                request_key += ":" + key  # the broadcast copy's key shape
            existing = self.conn.execute(
                "SELECT id, content_hash FROM session_questions "
                "WHERE sender = 'owner' AND idempotency_key = ?",
                (request_key,),
            ).fetchone()
            if existing:
                if existing["content_hash"] != digest:
                    raise APIError(409, "request_key already used for different content")
                return str(existing["id"])
        notice_id = ("q-" if question else "b-") + secrets.token_hex(16)
        with self.conn:
            self.conn.execute(
                "INSERT INTO session_questions "
                "(id, sender, recipient, sender_name, root, question, created_at, "
                "expires_at, idempotency_key, content_hash, kind, priority) "
                "VALUES (?, 'owner', ?, 'You', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    notice_id,
                    key,
                    row["root"],
                    message,
                    int(time.time() * 1000),
                    NO_DEADLINE,
                    request_key or notice_id,
                    digest,
                    "question" if question else "broadcast",
                    int(priority),
                ),
            )
        return notice_id

    def mail_stats(self, since_ms: int) -> dict[str, int]:
        """Agent-to-agent questions sent since a time, and how many were answered."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS sent, COALESCE(SUM(status = 'answered'), 0) AS answered "
            "FROM session_questions WHERE kind = 'question' AND created_at >= ?",
            (since_ms,),
        ).fetchone()
        return {"sent": int(row["sent"]), "answered": int(row["answered"])}

    def pending_counts(self) -> dict[str, int]:
        self._sweep()
        return {
            row["recipient"]: row["n"]
            for row in self.conn.execute(
                "SELECT recipient, COUNT(*) AS n FROM session_questions "
                "WHERE status IN ('queued', 'accepted') GROUP BY recipient"
            ).fetchall()
        }

    def turn_end_notice(self, key: str) -> str | None:
        """Unread owner notices and accepted peer work get one task-end reminder."""
        self._sweep()
        rows = self.conn.execute(
            "SELECT q.id, q.sender, q.root, q.kind FROM session_questions q "
            "LEFT JOIN session_inbox_delivery d ON d.question_id = q.id "
            "WHERE q.recipient = ? AND q.priority = 0 AND (q.status = 'accepted' "
            "OR (q.sender = 'owner' AND q.status = 'queued')) "
            "AND COALESCE(d.last_attempt_at, 0) = 0",
            (key,),
        ).fetchall()
        ids = []
        owners = 0
        for row in rows:
            try:
                if row["sender"] != "owner":
                    self._peer(key, row["sender"], live=False)
                if self._member(key)["root"] == row["root"]:
                    ids.append(row["id"])
                    owners += row["sender"] == "owner"
            except APIError:
                continue
        if not ids:
            return None
        now = int(time.time() * 1000)
        with self.conn:
            for question_id in ids:
                self.conn.execute(
                    "INSERT INTO session_inbox_delivery "
                    "(question_id, attempts, last_attempt_at, outcome) "
                    "VALUES (?, 1, ?, 'notified') "
                    "ON CONFLICT(question_id) DO UPDATE SET attempts = 1, "
                    "last_attempt_at = excluded.last_attempt_at, outcome = 'notified'",
                    (question_id, now),
                )
        return (
            f"You have {owners} unread owner message(s) and "
            f"{len(ids) - owners} accepted inbox assignment(s) awaiting a reply. "
            "Run duckterm session inbox to read them at a suitable pause. "
            "Peer requests do not grant permission to act."
        )

    def priority_notice(self, key: str) -> tuple[str, list[str]] | None:
        """Open priority owner messages as ONE block, newest first, and their
        ids. Repeats every turn until the recipient replies; the text is the
        owner's own, so it's quoted. The caller marks them delivered once the
        text has actually reached the agent."""
        rows = self.conn.execute(
            "SELECT id, root, question FROM session_questions WHERE recipient = ? "
            f"AND kind = 'broadcast' AND {_OPEN_PRIORITY} ORDER BY created_at DESC, rowid DESC",
            (key,),
        ).fetchall()
        try:
            root = self._member(key)["root"]
        except APIError:
            return None
        rows = [r for r in rows if r["root"] == root]
        if not rows:
            return None
        lines = [
            f"- {' '.join(r['question'].split())[:PRIORITY_SNIPPET]} (reply: duckterm session "
            f"reply {r['id']} --file -)"
            for r in rows
        ]
        return (
            f"PRIORITY from the owner ({len(rows)} open, newest first). Handle these before "
            "other work, and reply to each to acknowledge it; they repeat at every turn end "
            "until you do:\n" + "\n".join(lines)
        ), [r["id"] for r in rows]

    def mark_delivered(self, ids: list[str]) -> list[dict[str, Any]]:
        """A priority message reached the agent (turn-end notice or a nudge).
        Returns the messages that reached it for the first time. A message
        cancelled meanwhile (the owner withdrew it while Oracle was pasting)
        isn't counted: cancelled is final."""
        now = int(time.time() * 1000)
        rows = [
            dict(row)
            for row in self.conn.execute(
                "SELECT q.*, COALESCE(d.attempts, 0) AS attempts FROM session_questions q "
                "LEFT JOIN session_inbox_delivery d ON d.question_id = q.id "
                f"WHERE q.id IN ({','.join('?' * len(ids))}) AND q.status != 'cancelled'",
                ids,
            )
        ]
        first = [_public_question(row) for row in rows if not row["attempts"]]
        with self.conn:
            for question_id in (row["id"] for row in rows):
                self.conn.execute(
                    "INSERT INTO session_inbox_delivery "
                    "(question_id, attempts, last_attempt_at, outcome) "
                    "VALUES (?, 1, ?, 'delivered') "
                    "ON CONFLICT(question_id) DO UPDATE SET attempts = attempts + 1, "
                    "last_attempt_at = excluded.last_attempt_at, outcome = 'delivered'",
                    (question_id, now),
                )
        return first

    def cancel_broadcast(self, request_key: str) -> int:
        """The owner withdraws a broadcast: every recipient's copy closes, which
        retires priority pins. Returns how many open copies closed."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE session_questions SET status = 'cancelled', answered_at = ? "
                "WHERE kind = 'broadcast' AND idempotency_key = ? || ':' || recipient "
                "AND status IN ('queued', 'read')",
                (int(time.time() * 1000), request_key),
            )
        return cur.rowcount

    def broadcast_status(self, request_key: str, can_pin: Callable[[str], bool]) -> dict[str, Any]:
        """Per recipient: acknowledged, delivered, pending next turn, inbox only,
        or cancelled. inbox only is for agents that can't take a pinned
        notice; it's never shown as delivered (contracts R1)."""
        rows = self.conn.execute(
            "SELECT q.id, q.recipient, q.status, q.priority, q.answered_at, "
            "COALESCE(d.attempts, 0) AS attempts, s.name FROM session_questions q "
            "LEFT JOIN session_inbox_delivery d ON d.question_id = q.id "
            "LEFT JOIN sessions s ON s.session_key = q.recipient "
            "WHERE q.kind = 'broadcast' AND q.idempotency_key = ? || ':' || q.recipient "
            "ORDER BY q.rowid",
            (request_key,),
        ).fetchall()
        if not rows:
            raise APIError(404, "broadcast not found")
        recipients = []
        for r in rows:
            if r["status"] == "answered":
                status = "acknowledged"
            elif r["status"] == "cancelled":
                status = "cancelled"
            elif not can_pin(r["recipient"]):
                status = "inbox only"
            elif r["attempts"]:
                status = "delivered"
            else:
                status = "pending next turn"
            recipients.append(
                {
                    "session_id": r["recipient"],
                    "name": r["name"],
                    "message_id": r["id"],
                    "status": status,
                }
            )
        return {
            "request_key": request_key,
            "priority": bool(rows[0]["priority"]),
            "recipients": recipients,
        }

    def open_mail(self, key: str) -> list[dict[str, Any]]:
        """Queued or accepted inbox records this session can still see, with
        the same scope checks the agent's own inbox read applies."""
        self._sweep()
        rows = self.conn.execute(
            "SELECT q.id, q.sender, q.root, q.kind, q.status, q.created_at, q.priority, "
            "q.question, COALESCE(d.last_read_at, 0) AS last_read_at FROM session_questions q "
            "LEFT JOIN session_inbox_delivery d ON d.question_id = q.id "
            "WHERE q.recipient = ? AND (q.status IN ('queued', 'accepted') "
            "OR (q.priority = 1 AND q.status = 'read'))",
            (key,),
        ).fetchall()
        mail = []
        for row in rows:
            try:
                if row["sender"] != "owner":
                    self._peer(key, row["sender"], live=False)
                if self._member(key)["root"] == row["root"]:
                    mail.append(dict(row))
            except APIError:
                continue
        return mail

    def card(self, key: str) -> dict[str, Any]:
        return self._public(self._member(key, live=False))

    def _session(self, key: str, *, live: bool = True) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM sessions WHERE session_key = ?", (key,)).fetchone()
        if row is None or (live and row["state"] in AT_REST_STATES):
            raise APIError(404, "session not available")
        return dict(row)

    def revoke(self, key: str, *, cancel_pending: bool = True) -> None:
        """Invalidate credentials; a resumable stop preserves request deadlines."""
        session_credentials.credential_path(key, self.credential_dir).unlink(missing_ok=True)
        self.conn.execute(
            "UPDATE session_api_members SET token_hash = ? WHERE session_key = ?",
            (secrets.token_hex(32), key),
        )
        if cancel_pending:
            self.conn.execute(
                "UPDATE session_questions SET status = 'cancelled', answered_at = ? "
                "WHERE (sender = ? OR recipient = ?) AND status IN ('queued', 'accepted')",
                (int(time.time() * 1000), key, key),
            )

    def enroll(self, key: str, req: dict[str, Any]) -> dict[str, Any]:
        session = self._session(key)
        folder = str(session.get("grp") or "")
        root = _text(req.get("root"), "root", 1024)
        if (
            not folder
            or not _inside(folder, root)
            or any(part in ("", ".", "..") for part in root.split("/"))
        ):
            raise APIError(400, "root must be this session's sidebar folder or an ancestor")
        alias = _text(req.get("api_name", key), "api_name", 128)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", alias):
            raise APIError(400, "api_name must use letters, digits, dot, underscore or hyphen")
        purpose = _text(req.get("purpose", ""), "purpose", 2048, empty=True)
        token = secrets.token_urlsafe(32)
        try:
            with self.conn:
                # Keep the old token file until the database accepts the replacement.
                self.conn.execute("DELETE FROM session_api_members WHERE session_key = ?", (key,))
                self.conn.execute(
                    "INSERT INTO session_api_members "
                    "(session_key, token_hash, root, folder, api_name, purpose, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        hashlib.sha256(token.encode()).hexdigest(),
                        root,
                        folder,
                        alias,
                        purpose,
                        int(time.time() * 1000),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise APIError(409, "api_name is already used in this collaboration root") from exc
        self._save_token(key, token)
        self.sync_memberships()
        self.conn.commit()
        return {"session_id": key, "token": token, "root": root, "api_name": alias}

    def _member(self, key: str, *, live: bool = True) -> dict[str, Any]:
        session = self._session(key, live=live)
        row = self.conn.execute(
            "SELECT * FROM session_api_members WHERE session_key = ?", (key,)
        ).fetchone()
        if row is None or row["folder"] != (session.get("grp") or ""):
            raise APIError(404, "session not available")
        return {**session, **dict(row), "session_updated_at": session["updated_at"]}

    def authenticate(self, headers: dict[str, str]) -> str:
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise APIError(401, "session credential required")
        digest = hashlib.sha256(auth[7:].encode()).hexdigest()
        row = self.conn.execute(
            "SELECT session_key FROM session_api_members WHERE token_hash = ?", (digest,)
        ).fetchone()
        if row is None:
            raise APIError(401, "invalid session credential")
        key = str(row["session_key"])
        try:
            self._member(key)
        except APIError as exc:
            raise APIError(401, "session credential is no longer active") from exc
        return key

    @staticmethod
    def _public(member: dict[str, Any]) -> dict[str, Any]:
        try:
            progress = json.loads(member.get("progress") or "{}")
            if not isinstance(progress, dict):
                progress = {}
        except (TypeError, ValueError):
            progress = {}
        summary = str(progress.get("summary") or "")
        name = member.get("name") or member.get("source_app") or member["session_key"]
        activity = member["activity"]
        if (member.get("progress_at") or 0) >= member["updated_at"] and summary:
            activity = summary
        return {
            "session_id": member["session_key"],
            "api_name": member["api_name"],
            "name": member.get("name") or member.get("source_app") or member["session_key"],
            "purpose": member["purpose"] or summary or name,
            "folder": member["folder"],
            "root": member["root"],
            "cwd": member.get("cwd"),
            "state": member["state"],
            "activity": activity or summary or member["state"],
            "last_tool": member.get("last_tool"),
            "next_actions": progress.get("next_actions", []),
            "deliverables": progress.get("deliverables", []),
            "updated_at": max(
                member["updated_at"],
                member.get("session_updated_at", 0),
                member.get("progress_at") or 0,
            ),
            "capabilities": [
                "inbox.read",
                "answers.explicit",
                "artifacts.register",
                "artifacts.list",
                "artifacts.read",
            ],
        }

    def _peer(self, sender: str, recipient: str, *, live: bool = True) -> dict[str, Any]:
        source = self._member(sender, live=live)
        target = self._member(recipient, live=live)
        if not source["root"] or source["root"] != target["root"]:
            raise APIError(404, "session not available")
        return target

    def _sweep(self) -> None:
        self.before_retire()
        now = int(time.time() * 1000)
        with self.conn:
            self.conn.execute(
                "UPDATE session_questions SET status = 'expired' "
                "WHERE status IN ('queued', 'accepted') AND expires_at > 0 AND expires_at <= ?",
                (now,),
            )
            mail_analytics.retire_mail(
                self.conn,
                "status NOT IN ('queued', 'accepted') "
                f"AND NOT {_OPEN_PRIORITY} "
                "AND CASE WHEN expires_at > 0 AND expires_at < ? THEN expires_at "
                "ELSE COALESCE(answered_at, created_at) END < ?",
                (NO_DEADLINE, now - 7 * 86400000),
            )
            mail_analytics.retire_mail(
                self.conn,
                f"kind = 'broadcast' AND created_at < ? AND NOT {_OPEN_PRIORITY}",
                (now - 7 * 86400000,),
            )
            self.conn.execute(
                "DELETE FROM session_broadcasts WHERE created_at < ?", (now - 7 * 86400000,)
            )
            self.conn.execute(
                "DELETE FROM session_inbox_delivery WHERE question_id NOT IN "
                "(SELECT id FROM session_questions)"
            )

    def inbox(self, key: str, *, owner: bool = False, before: int | None = None) -> dict[str, Any]:
        self._session(key, live=not owner)
        if before is not None and not 0 < before <= 9223372036854775807:
            raise APIError(400, "invalid cursor")
        self._sweep()
        # Sequence cursor uses SQLite rowid, avoiding same-millisecond pagination gaps.
        rows = self.conn.execute(
            "SELECT rowid AS sequence, * FROM session_questions WHERE recipient = ? "
            "AND rowid < ? ORDER BY rowid DESC LIMIT 51",
            (key, before if before is not None else 9223372036854775807),
        ).fetchall()
        messages = []
        for row in rows[:50]:
            if not owner:
                try:
                    if row["sender"] != "owner":
                        self._peer(key, row["sender"], live=False)
                    if self._member(key)["root"] != row["root"]:
                        continue
                except APIError:
                    continue
            message = _public_question(dict(row))
            delivery = self.conn.execute(
                "SELECT attempts, last_attempt_at, last_read_at, outcome "
                "FROM session_inbox_delivery "
                "WHERE question_id = ?",
                (row["id"],),
            ).fetchone()
            message["delivery"] = (
                dict(delivery) if delivery else {"attempts": 0, "outcome": "pending"}
            )
            messages.append(message)
            if not owner and row["status"] in ("queued", "accepted"):
                self.conn.execute(
                    "INSERT INTO session_inbox_delivery (question_id, last_read_at) VALUES (?, ?) "
                    "ON CONFLICT(question_id) DO UPDATE SET last_read_at = excluded.last_read_at",
                    (row["id"], int(time.time() * 1000)),
                )
            if not owner and row["kind"] == "broadcast" and row["status"] == "queued":
                self.conn.execute(
                    "UPDATE session_questions SET status = 'read' WHERE id = ?", (row["id"],)
                )
                message["status"] = "read"
        self.conn.commit()
        cursor = rows[49]["sequence"] if len(rows) > 50 else None
        result: dict[str, Any] = {"messages": messages, "next_cursor": cursor}
        if owner:
            try:
                result["card"] = self.card(key)
            except APIError:
                result["card"] = None
        return result

    def folder_inbox(self, folder: str, *, before: int | None = None) -> dict[str, Any]:
        """Owner-only history for either participant in a folder's current subtree.

        Do not filter by live state or enrollment: completed interactions remain
        useful after a session stops. Membership includes each exchange only once.
        """
        if before is not None and not 0 < before <= 9223372036854775807:
            raise APIError(400, "invalid cursor")
        self._sweep()
        prefix = folder + "/"
        rows = self.conn.execute(
            "WITH members AS (SELECT session_key FROM sessions "
            "WHERE grp = ? OR substr(grp, 1, ?) = ?) "
            "SELECT q.rowid AS sequence, q.*, "
            "COALESCE(r.name, r.session_key, q.recipient) AS recipient_name "
            "FROM session_questions q LEFT JOIN sessions r ON r.session_key = q.recipient "
            "WHERE q.rowid < ? AND (q.sender IN (SELECT session_key FROM members) "
            "OR q.recipient IN (SELECT session_key FROM members)) "
            "ORDER BY q.rowid DESC LIMIT 51",
            (folder, len(prefix), prefix, before if before is not None else 9223372036854775807),
        ).fetchall()
        return {
            "messages": [
                {**_public_question(dict(row)), "recipient_name": row["recipient_name"]}
                for row in rows[:50]
            ],
            "next_cursor": rows[49]["sequence"] if len(rows) > 50 else None,
        }

    def _question(self, key: str, request_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM session_questions WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise APIError(404, "question not found")
        participants = {row["recipient"]}
        if row["sender"] != "owner":
            participants.add(row["sender"])
        if key not in participants:
            raise APIError(404, "question not found")
        if row["sender"] != "owner":
            self._peer(row["sender"], row["recipient"], live=False)
        if self._member(key)["root"] != row["root"]:
            raise APIError(404, "question not found")
        return _public_question(dict(row))

    def handle(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, Any]]:
        key = self.authenticate(headers)
        self._sweep()
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path.removeprefix("/api/v1/session")
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        limit = MAX_ARTIFACT_REQUEST_BYTES if path == "/artifacts" else MAX_BODY_BYTES
        if len(body) > limit:
            raise APIError(413, "request body too large")
        try:
            req = json.loads(body or b"{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise APIError(400, "invalid JSON") from exc
        if not isinstance(req, dict):
            raise APIError(400, "expected a JSON object")
        member = self._member(key)
        if path == "/artifacts" and method in {"GET", "POST"}:
            try:
                if method == "GET":
                    if "folder" in query:
                        folder = _text(query["folder"][0], "folder", 4096)
                        if "\x00" in folder or any(
                            part in ("", ".", "..") for part in folder.split("/")
                        ):
                            raise APIError(400, "invalid folder path")
                        if not member["root"] or not _inside(folder, member["root"]):
                            raise APIError(403, "folder exceeds the granted shared ancestor")
                        rows = self.artifacts.list_folder(folder, 501, shared_root=member["root"])
                        return 200, {"artifacts": rows[:500], "truncated": len(rows) > 500}
                    return 200, {"artifacts": self.artifacts.list(key)}
                return 200, {"artifact": self.artifacts.register(key, req)}
            except ArtifactError as exc:
                raise APIError(exc.status, str(exc)) from exc
        artifact_match = re.fullmatch(r"/artifacts/([a-f0-9]{32})", path)
        if artifact_match and method == "GET":
            artifact_id = artifact_match[1]
            row = self.conn.execute(
                "SELECT a.session_key FROM artifacts a JOIN sessions s "
                "ON s.session_key = a.session_key WHERE a.id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                raise APIError(404, "Artifact not found")
            if row["session_key"] != key:
                # Saved work remains reviewable after its producer stops, but
                # access always follows both sessions' current sharing grants.
                try:
                    self._peer(key, row["session_key"], live=False)
                except APIError as exc:
                    raise APIError(404, "Artifact not found") from exc
            try:
                return 200, {"artifact": self.artifacts.get(row["session_key"], artifact_id)}
            except ArtifactError as exc:
                raise APIError(exc.status, str(exc)) from exc
        if path == "/self" and method == "GET":
            return 200, self._public(member)
        if path == "/self" and method == "PATCH":
            if set(req) - {"purpose", "activity"}:
                raise APIError(400, "only purpose and activity can be updated")
            purpose = _text(req.get("purpose", member["purpose"]), "purpose", 2048, empty=True)
            activity = _text(req.get("activity", member["activity"]), "activity", 2048, empty=True)
            with self.conn:
                self.conn.execute(
                    "UPDATE session_api_members SET purpose = ?, activity = ?, updated_at = ? "
                    "WHERE session_key = ?",
                    (purpose, activity, int(time.time() * 1000), key),
                )
            return 200, self._public(self._member(key))
        if path == "/peers" and method == "GET":
            scope = query.get("scope", ["self_folder"])[0]
            levels = {"self_folder": 0, "parent": 1, "grandparent": 2}
            if scope not in levels and scope != "shared_root":
                raise APIError(400, "scope must be self_folder, parent, grandparent or shared_root")
            if not member["root"]:
                return 200, {"sessions": [], "next_cursor": None}
            parts = member["folder"].split("/")
            depth = len(parts) - levels.get(scope, 0)
            folder = (
                member["root"]
                if scope == "shared_root"
                else ("/".join(parts[:depth]) if depth > 0 else "")
            )
            if not folder or not _inside(folder, member["root"]):
                raise APIError(403, "scope exceeds the granted shared ancestor")
            peer_cursor = _text(query.get("cursor", [""])[0], "cursor", 128, empty=True)
            peers = []
            for row in self.conn.execute(
                "SELECT session_key FROM session_api_members "
                "WHERE root = ? AND session_key > ? ORDER BY session_key",
                (member["root"], peer_cursor),
            ).fetchall():
                if row["session_key"] == key:
                    continue
                try:
                    target = self._member(row["session_key"])
                except APIError:
                    continue
                if _inside(target["folder"], folder):
                    peers.append(self._public(target))
                    if len(peers) == 51:
                        break
            return 200, {
                "sessions": peers[:50],
                "next_cursor": peers[49]["session_id"] if len(peers) > 50 else None,
            }
        if path == "/inbox" and method == "GET":
            try:
                cursor = int(query["before"][0]) if "before" in query else None
            except ValueError as exc:
                raise APIError(400, "invalid cursor") from exc
            return 200, self.inbox(key, before=cursor)
        if path == "/questions" and method == "POST":
            return self._ask(key, req, headers)
        segments = path.strip("/").split("/")
        if len(segments) in (2, 3) and segments[0] == "questions":
            question = self._question(key, segments[1])
            if len(segments) == 2 and method == "GET":
                return 200, question
            if len(segments) == 3 and method == "POST":
                return self._transition(key, question, segments[2], req)
        raise APIError(404, "endpoint not found")

    def _ask(
        self, key: str, req: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        if set(req) - {"target_session_id", "question", "timeout_seconds"}:
            raise APIError(400, "unknown question fields")
        target = _text(req.get("target_session_id"), "target_session_id", 128)
        question = _text(req.get("question"), "question", 16384)
        idem = _text(headers.get("idempotency-key"), "Idempotency-Key", 128)
        timeout = req.get("timeout_seconds", 0)
        if type(timeout) is not int or not 0 <= timeout <= 604800:
            raise APIError(400, "timeout_seconds must be 0 (persistent) or 1 to 604800")
        if target == key:
            raise APIError(400, "cannot ask your own session")
        self._peer(key, target)
        digest = hashlib.sha256(json.dumps([target, question, timeout]).encode()).hexdigest()
        old = self.conn.execute(
            "SELECT * FROM session_questions WHERE sender = ? AND idempotency_key = ?", (key, idem)
        ).fetchone()
        if old:
            if old["content_hash"] != digest:
                raise APIError(409, "Idempotency-Key already used for different content")
            return 200, self._question(key, old["id"])
        now = int(time.time() * 1000)
        pending = self.conn.execute(
            "SELECT COUNT(*) FROM session_questions WHERE (sender = ? OR recipient = ?) "
            "AND kind = 'question' AND status IN ('queued', 'accepted')",
            (key, target),
        ).fetchone()[0]
        recent = self.conn.execute(
            "SELECT COUNT(*) FROM session_questions WHERE sender = ? "
            "AND kind = 'question' AND created_at > ?",
            (key, now - 60000),
        ).fetchone()[0]
        if pending >= 20 or recent >= 10:
            raise APIError(429, "question limit reached; try again later")
        source = self._member(key)
        request_id = "q-" + secrets.token_hex(16)
        with self.conn:
            self.conn.execute(
                "INSERT INTO session_questions (id, sender, recipient, sender_name, root, "
                "question, created_at, expires_at, idempotency_key, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    key,
                    target,
                    self._public(source)["name"],
                    source["root"],
                    question,
                    now,
                    now + timeout * 1000 if timeout else NO_DEADLINE,
                    idem,
                    digest,
                ),
            )
        return 202, self._question(key, request_id)

    def _transition(
        self, key: str, question: dict[str, Any], action: str, req: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        states = {
            "accept": "accepted",
            "answer": "answered",
            "decline": "declined",
            "cancel": "cancelled",
        }
        if question["kind"] == "broadcast" and action != "answer":
            raise APIError(400, "owner notices need no acceptance; replying is optional")
        if action not in states:
            raise APIError(404, "endpoint not found")
        actor = question["sender"] if action == "cancel" else question["recipient"]
        if key != actor:
            raise APIError(404, "question not found")
        answer = _text(req.get("text"), "text", 262144) if action in ("answer", "decline") else None
        state = states[action]
        if question["status"] == state and question["answer"] == answer:
            return 200, question
        allowed = ("queued", "read") if question["kind"] == "broadcast" else ("queued", "accepted")
        if question["status"] not in allowed:
            raise APIError(409, "question is already closed")
        with self.conn:
            self.conn.execute(
                "UPDATE session_questions SET status = ?, answer = ?, answered_at = ? WHERE id = ?",
                (
                    state,
                    answer,
                    int(time.time() * 1000) if state != "accepted" else None,
                    question["id"],
                ),
            )
        return 200, self._question(key, question["id"])
