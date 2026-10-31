"""Reviewed fork summaries use the existing priority-message delivery broker."""

import json
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError, _text
from duckterm.persistence.saved_state import current_revision
from duckterm.runtimes.base import AT_REST_STATES

if TYPE_CHECKING:
    from duckterm.persistence.history import HistoryStore


class ForkMerges:
    def __init__(self, history: "HistoryStore") -> None:
        self.history = history
        self.conn = history._conn

    def preview(self, key: str, can_pin: Callable[[str], bool]) -> dict[str, Any]:
        child = self.history.session(key)
        destination = child and (child.get("parent_session_key") or child.get("merged_into"))
        if not child or not destination:
            raise APIError(404, "This session is not a fork")
        parent = self.history.session(destination)
        reason = None
        if parent is None:
            reason = "The original parent was deleted. This fork remains readable."
        elif child["state"] in ("archived", "merged"):
            reason = "This child is already closed. Its merge history remains readable."
        elif self.closing(key):
            reason = "A saved merge still needs to close this child. Finish it from History."
        elif parent["state"] in AT_REST_STATES:
            reason = "The parent is not running. Resume it before merging."
        elif not any(
            t["session_id"] == parent["session_key"] and t["eligible"]
            for t in self.history.session_api.broadcast_targets(parent.get("grp") or "")
        ):
            reason = "The parent needs an inbox in a shared folder before merging."
        lines = ["Summary of the fork, not the full thread."]
        try:
            progress = json.loads(child.get("progress") or "{}")
        except (ValueError, TypeError):
            progress = {}
        if not isinstance(progress, dict):
            progress = {}
        if progress.get("summary"):
            lines += ["", str(progress["summary"])]
        for field, label in [
            ("deliverables", "Completed"),
            ("learnings", "Learned"),
            ("next_actions", "Next"),
        ]:
            if progress.get(field):
                lines += ["", label + ":", *["- " + str(item) for item in progress[field]]]
        checkpoints = self.history.checkpoints(key)
        if checkpoints:
            lines += ["", "Latest checkpoint:", checkpoints[0]["summary"]]
        if child.get("notes"):
            lines += ["", "Notes:", child["notes"]]
        if child.get("worktree_path"):
            lines += [
                "",
                f"Branch: {child.get('branch') or 'Not reported'}",
                "Code changes go through git or a pull request.",
            ]
        return {
            "child": {
                "key": key,
                "name": child.get("name") or key,
                "branch": child.get("branch") if child.get("worktree_path") else None,
            },
            "parent": (
                {
                    "key": parent["session_key"],
                    "name": parent.get("name") or parent["session_key"],
                    "model": parent.get("model"),
                    "state": parent["state"],
                    "priorityDelivery": can_pin(parent["session_key"]),
                }
                if parent
                else None
            ),
            "summary": "\n".join(lines),
            "memory": {"sourceSession": key, "summaryRevision": current_revision(child)},
            "allowed": reason is None,
            "reason": reason,
        }

    def prepare(
        self, child_key: str, request: dict[str, Any], can_pin: Callable[[str], bool]
    ) -> dict[str, Any]:
        required = {"summary", "keepOpen", "requestKey"}
        if not required <= set(request) or set(request) - required - {"summaryRevision"}:
            raise APIError(400, "Expected summary, keepOpen and requestKey")
        summary = _text(request["summary"], "summary", 16384)
        expected = request.get("summaryRevision")
        if expected is not None:
            _text(expected, "summaryRevision", 128)
        from duckterm import memory_access
        from duckterm.persistence.digests import DigestStore

        # HistoryStore is also used without a Server (migration/recovery).
        # Reuse its existing digest schema before recording a memory link.
        if not self._has_digests():
            DigestStore(conn=self.conn)

        unique = _text(request["requestKey"], "requestKey", 64)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", unique) or not isinstance(
            request["keepOpen"], bool
        ):
            raise APIError(400, "Invalid request key or keepOpen flag")
        key = f"merge:{child_key}:{unique}"
        _text(key, "request key", 128)
        row = self.conn.execute("SELECT * FROM fork_merges WHERE id = ?", (key,)).fetchone()
        if row:
            if row["summary"] != summary or bool(row["keep_open"]) != request["keepOpen"]:
                raise APIError(409, "requestKey already used for different content")
            saved = dict(row)
            if "summaryRevision" in request:
                link = self.conn.execute(
                    "SELECT text FROM digest_items WHERE id=? AND bucket=?",
                    (key + "-memory", memory_access.BUCKET),
                ).fetchone()
                if (json.loads(link["text"]).get("summaryRevision") if link else None) != expected:
                    raise APIError(409, "requestKey already used for a different summary revision")
        else:
            preview = self.preview(child_key, can_pin)
            if not preview["allowed"]:
                raise APIError(409, preview["reason"])
            child = self.history.session(child_key) or {}
            if not request["keepOpen"] and not child.get("launched"):
                raise APIError(409, "This child is observe-only. Keep it open to send a summary.")
            saved = {
                "id": key,
                "child": child_key,
                "parent": preview["parent"]["key"],
                "summary": summary,
                "keep_open": int(request["keepOpen"]),
                "created_at": int(time.time() * 1000),
                "message_id": None,
            }
            access = memory_access.grant(self.history, child_key, saved["parent"])
            if "summaryRevision" in request and expected != access["summaryRevision"]:
                raise APIError(409, "The source summary changed. Review the merge again.")
            # Legacy clients supply edited text without identifying its source
            # revision. Do not invent provenance by pointing at today's summary.
            access["summaryRevision"] = expected
            _text(
                summary + "\n\n" + memory_access.instructions(child_key, access["summaryRevision"]),
                "merge message",
                16384,
            )
            with self.conn:
                self.conn.execute(
                    "INSERT INTO fork_merges (id, child, parent, summary, keep_open, created_at) "
                    "VALUES (:id, :child, :parent, :summary, :keep_open, :created_at)",
                    saved,
                )
                memory_access.save(self.history, key + "-memory", access, saved["created_at"])
        if not saved["message_id"]:
            # Saving intent first binds the close-child choice across a crash.
            # Recover a message written by an older version before the intent's
            # message_id was committed; its body must not be rewritten on retry.
            prior = self.conn.execute(
                "SELECT id FROM session_questions WHERE sender='owner' AND idempotency_key=?",
                (key + ":" + saved["parent"],),
            ).fetchone()
            link = self.conn.execute(
                "SELECT text FROM digest_items WHERE id=? AND bucket=?",
                (key + "-memory", memory_access.BUCKET),
            ).fetchone()
            # Old intents retain their original message body. New intents bind
            # the revision at review even if progress advances before a retry.
            packet = (
                summary
                if not link
                else summary
                + "\n\n"
                + memory_access.instructions(
                    child_key, json.loads(link["text"]).get("summaryRevision")
                )
            )
            mid = (
                prior["id"]
                if prior
                else self.history.session_api.owner_message(
                    saved["parent"], packet, request_key=key, priority=True, merged_from=child_key
                )
            )
            self.conn.execute("UPDATE fork_merges SET message_id = ? WHERE id = ?", (mid, key))
            self.conn.commit()
            saved["message_id"] = mid
        return saved

    def status(self, child: str, key: str, can_pin: Callable[[str], bool]) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM fork_merges WHERE id = ? AND child = ?", (key, child)
        ).fetchone()
        if not row or not row["message_id"]:
            raise APIError(404, "Merge record not found")
        available = True
        try:
            result = self.history.session_api.broadcast_status(key, can_pin)
            status = result["recipients"][0]["status"]
            self.conn.execute("UPDATE fork_merges SET delivery = ? WHERE id = ?", (status, key))
            self.conn.commit()
        except APIError as exc:
            if exc.status != 404:
                raise
            available = False
            status = row["delivery"]
        self.refresh_checkpoints()
        checkpoint = self.conn.execute(
            "SELECT 1 FROM checkpoints WHERE id = ?", ("merge-" + row["message_id"],)
        ).fetchone()
        closed = (self.history.session(child) or {}).get("state") == "merged"
        link = (
            self.conn.execute(
                "SELECT text FROM digest_items WHERE id=? AND bucket='session_memory_link_v1'",
                (key + "-memory",),
            ).fetchone()
            if self._has_digests()
            else None
        )
        return {
            "id": key,
            "summary": row["summary"],
            "keepOpen": bool(row["keep_open"]),
            "delivery": status,
            "statusAvailable": available,
            "childClosed": closed,
            "memorySource": child,
            "summaryRevision": json.loads(link["text"]).get("summaryRevision") if link else None,
            "closeState": ("closed" if closed else "pending") if not row["keep_open"] else None,
            "checkpoint": (
                "created"
                if checkpoint
                else (
                    "not created"
                    if status == "cancelled" or not self.history.session(row["parent"])
                    else "pending"
                )
            ),
        }

    def refresh_checkpoints(self) -> None:
        """Recover a delivery/checkpoint crash gap before broker records retire."""
        self.conn.execute(
            "UPDATE fork_merges SET message_id = (SELECT q.id FROM session_questions q WHERE "
            "q.sender = 'owner' AND q.idempotency_key = fork_merges.id || ':' || "
            "fork_merges.parent) WHERE message_id IS NULL"
        )
        rows = self.conn.execute(
            "SELECT m.*, q.status, d.attempts FROM fork_merges m "
            "JOIN session_questions q ON q.id = m.message_id "
            "LEFT JOIN session_inbox_delivery d ON d.question_id = q.id"
        ).fetchall()
        for row in rows:
            if row["attempts"] or row["status"] == "answered":
                self.checkpoint(dict(row))
            if row["status"] in ("answered", "cancelled"):
                self.conn.execute(
                    "UPDATE fork_merges SET delivery = ? WHERE id = ?",
                    ("acknowledged" if row["status"] == "answered" else "cancelled", row["id"]),
                )
            elif row["attempts"]:
                self.conn.execute(
                    "UPDATE fork_merges SET delivery = 'delivered' WHERE id = ?", (row["id"],)
                )
        self.conn.commit()

    def checkpoint(self, row: dict[str, Any]) -> None:
        if not self.history.session(row["parent"]):
            return
        record = {
            "kind": "merge",
            "format": "checkpoint_fork_merge_v1",
            "from_session": row["child"],
            "request_key": row["id"],
            "message_id": row["message_id"],
        }
        self.conn.execute(
            "INSERT OR IGNORE INTO checkpoints (id, session_key, label, summary, record_json,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "merge-" + row["message_id"],
                row["parent"],
                "Merge from fork",
                "",
                json.dumps(record),
                int(time.time() * 1000),
            ),
        )

    def delivered(self, key: str) -> None:
        row = self.conn.execute("SELECT * FROM fork_merges WHERE id = ?", (key,)).fetchone()
        if row:
            self.checkpoint(dict(row))
            self.conn.execute("UPDATE fork_merges SET delivery = 'delivered' WHERE id = ?", (key,))
            self.conn.commit()

    def _has_digests(self) -> bool:
        return bool(
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='digest_items'"
            ).fetchone()
        )

    def closing(self, key: str) -> bool:
        return bool(
            self.conn.execute(
                "SELECT 1 FROM fork_merges WHERE child = ? AND keep_open = 0 AND message_id "
                "IS NOT NULL",
                (key,),
            ).fetchone()
            or (
                self._has_digests()
                and self.conn.execute(
                    "SELECT 1 FROM digest_items WHERE session_key=? AND bucket='agent_merge_v1' "
                    "AND json_extract(text,'$.closeSource')=1 LIMIT 1",
                    (key,),
                ).fetchone()
            )
        )

    def finish(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE sessions SET merged_into = ? WHERE session_key = ?",
            (row["parent"], row["child"]),
        )
        self.conn.commit()

    def list(self, key: str, can_pin: Callable[[str], bool]) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM fork_merges WHERE (child = ? OR parent = ?) AND message_id IS NOT "
            "NULL ORDER BY created_at DESC",
            (key, key),
        ).fetchall()
        return [
            {
                **self.status(row["child"], row["id"], can_pin),
                "child": row["child"],
                "parent": row["parent"],
                "createdAt": row["created_at"],
                "parentDeleted": self.history.session(row["parent"]) is None,
            }
            for row in rows
        ]
