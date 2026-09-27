"""Persistent outcomes, separate from inbox replies. Never executes assigned work."""

from __future__ import annotations

import json
import re
import secrets
import time
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError, _text

if TYPE_CHECKING:
    from duckterm.core.session_api import SessionAPI

HOUR = 3_600_000
STATES = {"proposed", "accepted", "in_progress", "blocked", "done", "dropped"}
CLOSED = {"done", "dropped"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS session_work (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, origin_request_id TEXT NOT NULL UNIQUE,
 requester TEXT NOT NULL, owner_session TEXT, root TEXT NOT NULL,
 state TEXT NOT NULL, blocker TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '',
 owner_authorized INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, closed_at INTEGER,
 last_notice_at INTEGER NOT NULL DEFAULT 0, last_status_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS work_assignee ON session_work(owner_session, state);
CREATE TABLE IF NOT EXISTS session_work_events (
 id INTEGER PRIMARY KEY, work_id TEXT NOT NULL, actor TEXT NOT NULL,
 state TEXT NOT NULL, note TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS session_work_updates (
 id INTEGER PRIMARY KEY, recipient TEXT NOT NULL, root TEXT NOT NULL,
 event_key TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,
 read_at INTEGER, UNIQUE(recipient, event_key)
);
"""


class WorkItems:
    def __init__(self, api: SessionAPI) -> None:
        self.api = api
        self.conn = api.conn
        self.conn.executescript(SCHEMA)

    def _visible(self, key: str | None, row: dict[str, Any]) -> bool:
        if key is None:
            return True  # owner-authenticated server endpoint only
        member = self.api._member(key, live=False)
        return member["root"] == row["root"] and key in {row["requester"], row["owner_session"]}

    def get(self, key: str | None, work_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM session_work WHERE id = ?", (work_id,)).fetchone()
        if row is None or not self._visible(key, dict(row)):
            raise APIError(404, "work item not found")
        result = dict(row)
        result["history"] = [
            dict(e)
            for e in self.conn.execute(
                "SELECT actor, state, note, created_at FROM session_work_events "
                "WHERE work_id = ? ORDER BY id",
                (work_id,),
            )
        ]
        return result

    def listing(self, key: str | None, *, before: int | None = None) -> dict[str, Any]:
        if before is not None and not 0 < before <= 9223372036854775807:
            raise APIError(400, "invalid cursor")
        args: list[Any] = [before if before is not None else 9223372036854775807]
        scope = ""
        if key is not None:
            scope = " AND root = ? AND (requester = ? OR owner_session = ?)"
            args.extend([self.api._member(key, live=False)["root"], key, key])
        rows = self.conn.execute(
            "SELECT rowid AS sequence, * FROM session_work WHERE rowid < ?"
            + scope
            + " ORDER BY rowid DESC LIMIT 51",
            args,
        ).fetchall()
        return {
            "work": [dict(r) for r in rows[:50]],
            "next_cursor": rows[49]["sequence"] if len(rows) > 50 else None,
        }

    def create(self, key: str | None, request_id: str, title: Any) -> dict[str, Any]:
        title = _text(title, "title", 500)
        if key is None:
            raw = self.conn.execute(
                "SELECT * FROM session_questions WHERE id = ?", (request_id,)
            ).fetchone()
            if raw is None:
                raise APIError(404, "question not found")
            q = dict(raw)
        else:
            q = self.api._question(key, request_id)
        old = self.conn.execute(
            "SELECT id FROM session_work WHERE origin_request_id = ?", (request_id,)
        ).fetchone()
        if old:
            return self.get(key, old["id"])
        assignee = self.api._member(q["recipient"], live=False)
        requester = q["sender"] if q["kind"] != "broadcast" else q["recipient"]
        now = int(time.time() * 1000)
        state = "accepted" if q["status"] == "accepted" else "proposed"
        work_id = "w-" + secrets.token_hex(16)
        if (
            self.conn.execute(
                "SELECT COUNT(*) FROM session_work WHERE owner_session = ? AND "
                "state NOT IN ('done','dropped')",
                (q["recipient"],),
            ).fetchone()[0]
            >= 100
        ):
            raise APIError(429, "too many open work items for this session")
        with self.conn:
            self.conn.execute(
                "INSERT INTO session_work "
                "(id,title,origin_request_id,requester,owner_session,root,state,"
                "created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    work_id,
                    title,
                    request_id,
                    requester,
                    q["recipient"],
                    assignee["root"],
                    state,
                    now,
                    now,
                ),
            )
            self._event(work_id, key, state, "Created from inbox request", now)
        return self.get(key, work_id)

    def _event(self, work_id: str, actor: str | None, state: str, note: str, now: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO session_work_events "
            "(work_id,actor,state,note,created_at) VALUES (?,?,?,?,?)",
            (work_id, actor or "owner", state, note, now),
        )
        return int(cur.lastrowid or 0)

    def update(self, key: str | None, work_id: str, req: dict[str, Any]) -> dict[str, Any]:
        if set(req) - {"state", "note", "blocker", "evidence", "owner_session", "owner_authorized"}:
            raise APIError(400, "unknown work fields")
        row = self.get(key, work_id)
        if row["state"] in CLOSED:
            last_note = row["history"][-1]["note"] if row["history"] else ""
            if (
                set(req) <= {"state", "evidence", "note"}
                and req.get("state", row["state"]) == row["state"]
                and req.get("evidence", row["evidence"]) == row["evidence"]
                and req.get("note", last_note) == last_note
            ):
                return row
            raise APIError(409, "work item is closed")
        if "owner_authorized" in req and (
            key is not None or type(req["owner_authorized"]) is not bool
        ):
            raise APIError(403, "only the owner can authorize work")
        state = req.get("state", row["state"])
        if not isinstance(state, str) or state not in STATES:
            raise APIError(400, "invalid work state")
        note = _text(req.get("note", ""), "note", 500, empty=True).strip()
        assignee = row["owner_session"]
        forwarding = "owner_session" in req and req["owner_session"] != assignee
        if forwarding:
            assignee = _text(req["owner_session"], "owner_session", 128)
            target = self.api._member(assignee)
            if target["root"] != row["root"]:
                raise APIError(404, "session not available in work scope")
            if key is not None:
                self.api._peer(key, assignee)
            state = "proposed"  # forwarding is a handoff, never completion
            if not note:
                raise APIError(400, "forwarding requires a handoff note")
        elif key is not None and key != assignee and state not in {"dropped", "proposed"}:
            raise APIError(403, "only the assigned session can advance work")
        blocker = (
            _text(req.get("blocker", row["blocker"]), "blocker", 2000, empty=True).strip()
            if state == "blocked"
            else ""
        )
        evidence = _text(req.get("evidence", row["evidence"]), "evidence", 2000, empty=True).strip()
        if state == "blocked" and not blocker:
            raise APIError(400, "blocked work must name its blocker")
        if state == "done" and not re.search(
            r"(?:\b[0-9a-fA-F]{7,40}\b|https://\S+|#\d+\b|\bv\d+\.\d+\.\d+\b)", evidence
        ):
            raise APIError(
                400, "done requires evidence: a commit, PR, release, or verification reference"
            )
        if state == "dropped" and not note:
            raise APIError(400, "dropping work requires a reason")
        authorized = int(req.get("owner_authorized", row["owner_authorized"]))
        now = int(time.time() * 1000)
        if (state, assignee, blocker, evidence, authorized) == (
            row["state"],
            row["owner_session"],
            row["blocker"],
            row["evidence"],
            row["owner_authorized"],
        ) and (not note or (row["history"] and row["history"][-1]["note"] == note)):
            return row  # retries cannot reset staleness
        with self.conn:
            self.conn.execute(
                "UPDATE session_work SET "
                "state=?,owner_session=?,blocker=?,evidence=?,owner_authorized=?,"
                "updated_at=?,closed_at=?,last_status_at=0 WHERE id=?",
                (
                    state,
                    assignee,
                    blocker,
                    evidence,
                    authorized,
                    now,
                    now if state in CLOSED else None,
                    work_id,
                ),
            )
            audit_note = note
            if authorized != row["owner_authorized"]:
                audit_note = f"Owner authorization: {bool(authorized)}. {note}".strip()
            event = self._event(work_id, key, state, audit_note, now)
            payload = {
                "work_id": work_id,
                "request_id": row["origin_request_id"],
                "state": state,
                "note": note,
                "blocker": blocker,
                "evidence": evidence,
            }
            for recipient in {row["requester"], assignee} - {key, None}:
                self._notify(str(recipient), row["root"], f"work:{event}", payload, now)
            if state == "done":
                self.request_update(
                    row["origin_request_id"], "work_done", now, downstream_only=True
                )
        return self.get(key, work_id) if not forwarding else self.get(None, work_id)

    def _notify(
        self, recipient: str, root: str, event: str, payload: dict[str, Any], now: int
    ) -> None:
        # Scope may have changed since the request was created. Never leak across roots.
        try:
            if self.api._member(recipient, live=False)["root"] != root:
                return
        except APIError:
            return
        if payload.get("kind") == "stale":
            self.conn.execute(
                "DELETE FROM session_work_updates WHERE recipient=? AND read_at IS NULL "
                "AND json_extract(payload,'$.kind')='stale' "
                "AND json_extract(payload,'$.work_id')=?",
                (recipient, payload["work_id"]),
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO session_work_updates "
            "(recipient,root,event_key,payload,created_at) VALUES (?,?,?,?,?)",
            (recipient, root, event, json.dumps(payload), now),
        )

    def updates(self, key: str, *, mark_read: bool = True) -> list[dict[str, Any]]:
        root = self.api._member(key, live=False)["root"]
        rows = self.conn.execute(
            "SELECT * FROM session_work_updates WHERE recipient=? AND root=? AND "
            "read_at IS NULL ORDER BY id LIMIT 100",
            (key, root),
        ).fetchall()
        if mark_read:
            with self.conn:
                self.conn.executemany(
                    "UPDATE session_work_updates SET read_at=? WHERE id=?",
                    [(int(time.time() * 1000), r["id"]) for r in rows],
                )
        return [
            {"id": r["id"], "created_at": r["created_at"], **json.loads(r["payload"])} for r in rows
        ]

    def request_update(
        self, request_id: str, kind: str, now: int, *, downstream_only: bool = False
    ) -> None:
        origin = request_id
        seen: set[str] = set()
        for depth in range(5):
            if request_id in seen:
                break
            seen.add(request_id)
            row = self.conn.execute(
                "SELECT * FROM session_questions WHERE id=?", (request_id,)
            ).fetchone()
            if row is None or row["status"] in {"cancelled", "declined", "expired"}:
                break
            if row["kind"] != "broadcast" and (depth or not downstream_only):
                self._notify(
                    row["sender"],
                    row["root"],
                    f"request:{origin}:{request_id}:{kind}",
                    {"request_id": request_id, "kind": kind, "downstream": depth > 0},
                    now,
                )
            request_id = row["parent_request_id"]
            if not request_id:
                break

    def release_request(self, request_id: str) -> None:
        row = self.conn.execute(
            "SELECT * FROM session_work WHERE origin_request_id=? AND state NOT "
            "IN ('done','dropped') AND owner_session="
            "(SELECT recipient FROM session_questions WHERE id=?)",
            (request_id, request_id),
        ).fetchone()
        if row:
            now = int(time.time() * 1000)
            self.conn.execute(
                "UPDATE session_work SET state='proposed', owner_session=NULL, "
                "updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            self._event(
                row["id"],
                None,
                "proposed",
                "Request closed without completing work; reassignment needed",
                now,
            )
            self._notify(
                row["requester"],
                row["root"],
                f"unassigned:{row['id']}:{now}",
                {"work_id": row["id"], "state": "proposed", "note": "Work needs reassignment"},
                now,
            )

    def release(self, key: str) -> None:
        """A deleted/archived assignee does not delete the outcome or lose ownership history."""
        now = int(time.time() * 1000)
        for row in self.conn.execute(
            "SELECT * FROM session_work WHERE owner_session=? AND state NOT IN ('done','dropped')",
            (key,),
        ).fetchall():
            self.conn.execute(
                "UPDATE session_work SET "
                "owner_session=NULL,state='proposed',updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            self._event(
                row["id"],
                key,
                "proposed",
                "Assigned session became unavailable; reassignment needed",
                now,
            )
            self._notify(
                row["requester"],
                row["root"],
                f"release:{row['id']}:{now}",
                {
                    "work_id": row["id"],
                    "state": "proposed",
                    "note": "Assigned session unavailable; reassignment needed",
                },
                now,
            )

    def stale(self, key: str, now: int) -> list[dict[str, Any]]:
        root = self.api._member(key, live=False)["root"]
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM session_work WHERE owner_session=? AND root=? AND "
                "state IN ('accepted','in_progress') AND updated_at<=?",
                (key, root, now - HOUR),
            )
        ]

    def nudge_items(self, key: str, now: int) -> list[dict[str, Any]]:
        proposed = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM session_work WHERE owner_session=? AND root=? AND "
                "state='proposed' AND updated_at<=?",
                (key, self.api._member(key, live=False)["root"], now - 5 * 60_000),
            )
        ]
        return [
            {
                "id": f"work:{r['id']}:{r['updated_at']}:{(now-r['updated_at'])//HOUR}",
                "kind": "work",
                "work_id": r["id"],
                "status": "accepted",
                "created_at": r["updated_at"],
            }
            for r in self.stale(key, now) + proposed
            if now - r["last_notice_at"] >= HOUR
        ]

    def notified(self, items: list[dict[str, Any]], now: int) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE session_work SET last_notice_at=? WHERE id=?",
                [(now, item["work_id"]) for item in items if item.get("kind") == "work"],
            )

    def turn_notice(self, key: str) -> str | None:
        now = int(time.time() * 1000)
        rows = [r for r in self.stale(key, now) if now - r["last_notice_at"] >= HOUR]
        if not rows:
            return None
        with self.conn:
            self.conn.executemany(
                "UPDATE session_work SET last_notice_at=? WHERE id=?",
                [(now, r["id"]) for r in rows],
            )
        return (
            f"{len(rows)} work item(s) have no update for at least an hour. "
            "Run duckterm session work list, update progress or record a blocker, "
            "and continue your work."
        )

    def tick(self, now: int) -> None:
        # Sender learns the honest state even if a busy recipient has no turn boundary.
        with self.conn:
            orphaned = self.conn.execute(
                "SELECT w.* FROM session_work w LEFT JOIN session_api_members m "
                "ON m.session_key=w.owner_session WHERE w.owner_session IS NOT NULL "
                "AND w.state NOT IN ('done','dropped') AND (m.root IS NULL OR m.root<>w.root)"
            ).fetchall()
            for row in orphaned:
                self.conn.execute(
                    "UPDATE session_work SET owner_session=NULL,state='proposed',updated_at=? "
                    "WHERE id=?",
                    (now, row["id"]),
                )
                self._event(
                    row["id"],
                    None,
                    "proposed",
                    "Assignee left the shared scope; reassignment needed",
                    now,
                )
                self._notify(
                    row["requester"],
                    row["root"],
                    f"scope:{row['id']}:{now}",
                    {
                        "work_id": row["id"],
                        "state": "proposed",
                        "note": "Assignee left shared scope",
                    },
                    now,
                )
            for row in self.conn.execute(
                "SELECT * FROM session_work WHERE state IN "
                "('accepted','in_progress') AND updated_at<=? AND last_status_at<=?",
                (now - HOUR, now - HOUR),
            ).fetchall():
                self._notify(
                    row["requester"],
                    row["root"],
                    f"stale:{row['id']}:{now//HOUR}",
                    {
                        "work_id": row["id"],
                        "state": row["state"],
                        "kind": "stale",
                        "note": "No new progress update from the assigned session.",
                    },
                    now,
                )
                self.conn.execute(
                    "UPDATE session_work SET last_status_at=? WHERE id=?", (now, row["id"])
                )
            self.conn.execute(
                "DELETE FROM session_work_updates WHERE read_at IS NOT NULL AND read_at < ?",
                (now - 7 * 24 * HOUR,),
            )
