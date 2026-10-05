"""Coordinator authority: canonical folders, identities and durable questions.

No provider process, terminal, filesystem project or owner input is accessed here.
All SQLite work runs on the service's owner thread.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, cast

from duckterm.core.session_api import APIError, _text
from duckterm.runtimes.base import AT_REST_STATES

PROTOCOL = 1
LEASE_MS = 60_000
MAX_BATCH = 100
MAX_BYTES = 1024 * 1024
MAX_PENDING = 20
SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE computers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 incarnation TEXT NOT NULL, writer TEXT, lease_until INTEGER NOT NULL DEFAULT 0,
 seen_at INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE invitations (
 token_hash TEXT PRIMARY KEY, name TEXT NOT NULL, expires_at INTEGER NOT NULL,
 claimed_by TEXT, claim_digest TEXT, bindings TEXT NOT NULL
);
CREATE TABLE folders (
 id TEXT PRIMARY KEY, parent TEXT, name TEXT NOT NULL,
 UNIQUE(parent, name)
);
CREATE TABLE bindings (
 computer TEXT NOT NULL, local_path TEXT NOT NULL, folder TEXT NOT NULL,
 PRIMARY KEY(computer, local_path)
);
CREATE TABLE placements (
 ref TEXT PRIMARY KEY, folder TEXT, local_path TEXT NOT NULL
);
CREATE TABLE peers (
 ref TEXT PRIMARY KEY, computer TEXT NOT NULL, session_key TEXT NOT NULL,
 folder TEXT, root TEXT, card TEXT NOT NULL, seen_at INTEGER NOT NULL,
 live INTEGER NOT NULL, UNIQUE(computer,session_key)
);
CREATE TABLE questions (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 sender TEXT NOT NULL, recipient TEXT NOT NULL, root TEXT NOT NULL,
 question TEXT NOT NULL, status TEXT NOT NULL, answer TEXT,
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
 answered_at INTEGER, revision INTEGER NOT NULL DEFAULT 1,
 delivered INTEGER NOT NULL DEFAULT 0, read_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE operations (
 computer TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
 result TEXT NOT NULL, PRIMARY KEY(computer,id)
);
CREATE INDEX questions_recipient ON questions(recipient,sequence);
CREATE TABLE local_outbox (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 actor TEXT NOT NULL, operation TEXT NOT NULL, result TEXT,
 attempted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE local_cache (key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


def now() -> int:
    return int(time.time() * 1000)


def identifier(value: Any, field: str = "id") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise APIError(400, f"Invalid {field}")
    return value


def reference(computer: Any, key: Any) -> str:
    identifier(computer, "computer")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key):
        raise APIError(400, "Invalid session key")
    return f"peer:{computer}:{key}"


def split_reference(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise APIError(400, "Invalid session reference")
    bits = value.split(":")
    if len(bits) != 3 or bits[0] != "peer" or reference(bits[1], bits[2]) != value:
        raise APIError(400, "Invalid session reference")
    return bits[1], bits[2]


def folder_path(value: Any) -> str:
    value = _text(value, "folder", 4096, empty=True)
    if value and ("\x00" in value or any(p in ("", ".", "..") for p in value.split("/"))):
        raise APIError(400, "Invalid folder path")
    return str(value)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, PROTOCOL):
            self.conn.close()
            raise ValueError("Collaboration database needs a newer DuckTerm")
        if version == 0:
            self.conn.executescript("BEGIN;" + SCHEMA + f"PRAGMA user_version={PROTOCOL}; COMMIT;")
        path.chmod(0o600)
        self.conn.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        self.conn.close()

    def setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def configure(self, **values: Any) -> None:
        with self.conn:
            for key, value in values.items():
                self.conn.execute(
                    "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) "
                    "DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )

    def initialize(self, name: str) -> dict[str, Any]:
        if not self.setting("workspace"):
            self.configure(
                workspace=uuid.uuid4().hex, coordinator=True, name=_text(name, "name", 128)
            )
        return {"workspace_id": self.setting("workspace"), "protocol": PROTOCOL}

    def computer(self, name: str, computer_id: str | None = None) -> dict[str, Any]:
        if not self.setting("coordinator"):
            raise APIError(409, "This computer is not a coordinator")
        computer_id = identifier(computer_id) if computer_id else uuid.uuid4().hex
        token, incarnation = secrets.token_urlsafe(32), uuid.uuid4().hex
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO computers(id,name,token_hash,incarnation) VALUES (?,?,?,?)",
                    (
                        computer_id,
                        _text(name, "name", 128),
                        hashlib.sha256(token.encode()).hexdigest(),
                        incarnation,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise APIError(409, "Computer already connected; use explicit recovery") from exc
        return {
            "computer_id": computer_id,
            "token": token,
            "incarnation": incarnation,
            "workspace_id": self.setting("workspace"),
            "protocol": PROTOCOL,
        }

    def authenticate(self, token: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM computers WHERE token_hash=? AND revoked=0",
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
        if row is None:
            raise APIError(401, "Computer capability is invalid or revoked")
        return dict(row)

    def invite(self, name: str, bindings: list[dict[str, str]]) -> dict[str, Any]:
        """An owner approves a concrete folder mapping before issuing this ticket."""
        name = _text(name, "computer name", 128)
        if not isinstance(bindings, list) or len(bindings) > 1000:
            raise APIError(400, "Invalid folder mappings")
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {"local_path", "folder_id"}:
                raise APIError(400, "Invalid folder mapping")
            if not folder_path(binding["local_path"]):
                raise APIError(400, "Ungrouped cannot be shared")
            self.ancestry(binding["folder_id"])
        token = secrets.token_urlsafe(32)
        expires = now() + 300_000
        with self.conn:
            self.conn.execute(
                "INSERT INTO invitations VALUES (?,?,?,NULL,NULL,?)",
                (hashlib.sha256(token.encode()).hexdigest(), name, expires, json.dumps(bindings)),
            )
        return {
            "ticket": token,
            "expires_at": expires,
            "workspace_id": self.setting("workspace"),
            "protocol": PROTOCOL,
        }

    def claim(self, ticket: str, identity: dict[str, Any]) -> dict[str, Any]:
        """Retryable only by the same new identity after a lost response."""
        _text(ticket, "pairing ticket", 128)
        for field in ("computer_id", "incarnation", "workspace_id"):
            identifier(identity.get(field), field)
        token = _text(identity.get("token"), "computer capability", 128)
        if len(token) < 32:
            raise APIError(400, "Computer capability is too short")
        if (
            identity["workspace_id"] != self.setting("workspace")
            or identity.get("protocol") != PROTOCOL
        ):
            raise APIError(409, "Incompatible workspace or protocol")
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        ticket_hash = hashlib.sha256(ticket.encode()).hexdigest()
        with self.conn:
            row = self.conn.execute(
                "SELECT * FROM invitations WHERE token_hash=?", (ticket_hash,)
            ).fetchone()
            if row is None:
                raise APIError(401, "Invalid pairing ticket")
            if row["claimed_by"]:
                if row["claim_digest"] != digest:
                    raise APIError(409, "Pairing ticket already used")
                self.authenticate(token)
            else:
                if row["expires_at"] <= now():
                    raise APIError(401, "Pairing ticket expired; request another")
                try:
                    self.conn.execute(
                        "INSERT INTO computers(id,name,token_hash,incarnation) VALUES (?,?,?,?)",
                        (
                            identity["computer_id"],
                            row["name"],
                            hashlib.sha256(token.encode()).hexdigest(),
                            identity["incarnation"],
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    existing = self.authenticate(token)
                    if (
                        existing["id"] != identity["computer_id"]
                        or existing["incarnation"] != identity["incarnation"]
                    ):
                        raise APIError(409, "Computer identity already exists") from exc
                for binding in json.loads(row["bindings"]):
                    self.conn.execute(
                        "INSERT INTO bindings VALUES (?,?,?) "
                        "ON CONFLICT(computer,local_path) DO UPDATE SET folder=excluded.folder",
                        (identity["computer_id"], binding["local_path"], binding["folder_id"]),
                    )
                self.conn.execute(
                    "UPDATE invitations SET claimed_by=?,claim_digest=? WHERE token_hash=?",
                    (identity["computer_id"], digest, ticket_hash),
                )
        return {
            "computer_id": identity["computer_id"],
            "workspace_id": identity["workspace_id"],
            "protocol": PROTOCOL,
            "connected": True,
        }

    def add_folder(self, name: str, parent: str | None = None) -> str:
        name = _text(name, "folder name", 256)
        if "/" in name or name in (".", "..") or "\x00" in name:
            raise APIError(400, "Invalid folder name")
        if parent:
            self.ancestry(parent)
        old = self.conn.execute(
            "SELECT id FROM folders WHERE parent IS ? AND name=?", (parent, name)
        ).fetchone()
        if old:
            return str(old[0])
        key = uuid.uuid4().hex
        self.conn.execute("INSERT INTO folders VALUES (?,?,?)", (key, parent, name))
        return key

    def ancestry(self, folder: str) -> list[str]:
        result: list[str] = []
        while folder:
            if folder in result:
                raise APIError(409, "Invalid folder ancestry")
            row = self.conn.execute("SELECT * FROM folders WHERE id=?", (folder,)).fetchone()
            if row is None:
                raise APIError(404, "Folder not found")
            result.append(folder)
            folder = row["parent"]
        return result

    def path(self, folder: str | None) -> str:
        if not folder:
            return ""
        return "/".join(
            str(self.conn.execute("SELECT name FROM folders WHERE id=?", (key,)).fetchone()[0])
            for key in reversed(self.ancestry(folder))
        )

    def bind(self, computer: str, local_path: str, folder: str) -> None:
        local_path = folder_path(local_path)
        if not local_path:
            raise APIError(400, "Ungrouped sessions cannot be shared by a folder binding")
        if not self.conn.execute(
            "SELECT 1 FROM computers WHERE id=? AND revoked=0", (computer,)
        ).fetchone():
            raise APIError(404, "Computer not found")
        self.ancestry(folder)
        with self.conn:
            self.conn.execute(
                "INSERT INTO bindings VALUES (?,?,?) ON CONFLICT(computer,local_path) "
                "DO UPDATE SET folder=excluded.folder",
                (computer, local_path, folder),
            )

    def place(self, ref: str, folder: str | None) -> None:
        split_reference(ref)
        if folder:
            self.ancestry(folder)
        with self.conn:
            peer = self.conn.execute("SELECT card FROM peers WHERE ref=?", (ref,)).fetchone()
            if peer is None:
                raise APIError(404, "Discover the session before moving it")
            local_path = json.loads(peer[0]).get("local_folder", "")
            self.conn.execute(
                "INSERT INTO placements VALUES (?,?,?) ON CONFLICT(ref) "
                "DO UPDATE SET folder=excluded.folder,local_path=excluded.local_path",
                (ref, folder, local_path),
            )
            self.conn.execute(
                "UPDATE peers SET folder=?,root=? WHERE ref=?",
                (folder, self.ancestry(folder)[-1] if folder else None, ref),
            )
            self.sweep()

    def revoke(self, computer: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE computers SET revoked=1,lease_until=0 WHERE id=?", (computer,)
            )
            self.conn.execute(
                "UPDATE peers SET live=0,folder=NULL,root=NULL WHERE computer=?", (computer,)
            )
            self.sweep()

    def change_folder(self, folder: str, parent: str | None, name: str) -> None:
        name = _text(name, "folder name", 256)
        if "/" in name or name in (".", "..") or "\x00" in name:
            raise APIError(400, "Invalid folder name")
        self.ancestry(folder)
        if parent and folder in self.ancestry(parent):
            raise APIError(400, "Cannot move a folder into itself")
        if self.conn.execute(
            "SELECT 1 FROM folders WHERE parent IS ? AND name=? AND id!=?",
            (parent, name, folder),
        ).fetchone():
            raise APIError(409, "Destination folder already exists")
        with self.conn:
            for peer in self.conn.execute(
                "SELECT * FROM peers WHERE folder IS NOT NULL"
            ).fetchall():
                if folder in self.ancestry(peer["folder"]):
                    local_path = json.loads(peer["card"])["local_folder"]
                    self.conn.execute(
                        "INSERT INTO bindings VALUES (?,?,?) "
                        "ON CONFLICT(computer,local_path) DO UPDATE SET folder=excluded.folder",
                        (peer["computer"], local_path, peer["folder"]),
                    )
            self.conn.execute(
                "UPDATE folders SET parent=?,name=? WHERE id=?", (parent, name, folder)
            )
            for row in self.conn.execute("SELECT * FROM peers WHERE folder IS NOT NULL").fetchall():
                ancestry = self.ancestry(row["folder"])
                card = json.loads(row["card"])
                narrow = card["local_root"] != card["local_folder"].split("/")[0]
                root = row["root"] if narrow and row["root"] in ancestry else ancestry[-1]
                self.conn.execute("UPDATE peers SET root=? WHERE ref=?", (root, row["ref"]))
            self.sweep()

    def mapped_folder(self, computer: str, card: dict[str, Any]) -> str | None:
        ref = reference(computer, card["session_key"])
        path = folder_path(card.get("folder", ""))
        override = self.conn.execute("SELECT * FROM placements WHERE ref=?", (ref,)).fetchone()
        if override:
            if override["local_path"] == path:
                return cast(str | None, override["folder"])
            # A later move on the owning computer supersedes the last coordinator
            # placement, including a move to Ungrouped while disconnected.
            self.conn.execute("DELETE FROM placements WHERE ref=?", (ref,))
        return self.mapped_path(computer, path)

    def mapped_path(self, computer: str, path: str) -> str | None:
        bindings = self.conn.execute(
            "SELECT * FROM bindings WHERE computer=? ORDER BY length(local_path) DESC", (computer,)
        ).fetchall()
        for binding in bindings:
            prefix = binding["local_path"]
            if path != prefix and not path.startswith(prefix + "/"):
                continue
            folder = binding["folder"]
            for child in path[len(prefix) :].strip("/").split("/"):
                if child:
                    folder = self.add_folder(child, folder)
            return str(folder)
        return None

    def advertise(self, computer: str, cards: list[dict[str, Any]]) -> None:
        if not isinstance(cards, list) or len(cards) > 10_000:
            raise APIError(413, "Too many session cards")
        self.conn.execute(
            "UPDATE peers SET live=0,folder=NULL,root=NULL WHERE computer=?", (computer,)
        )
        seen: set[str] = set()
        for card in cards:
            if not isinstance(card, dict):
                raise APIError(400, "Invalid session card")
            key = card.get("session_key")
            ref = reference(computer, key)
            if ref in seen:
                raise APIError(400, "Duplicate session card")
            seen.add(ref)
            clean = {
                k: _text(card.get(k, ""), k, n, empty=True)
                for k, n in [("name", 256), ("purpose", 2048), ("activity", 2048), ("state", 64)]
            }
            clean["local_folder"] = folder_path(card.get("folder", ""))
            clean["local_root"] = folder_path(card.get("root", clean["local_folder"]))
            folder = self.mapped_folder(computer, card)
            root = self.ancestry(folder)[-1] if folder else None
            # Preserve explicit narrower local grants. A coordinator placement
            # cannot silently broaden one of these grants.
            local_root = clean["local_root"]
            if not local_root:
                folder = root = None
            elif local_root != clean["local_folder"].split("/")[0]:
                root = self.mapped_path(computer, local_root)
                if not folder or root not in self.ancestry(folder):
                    folder = root = None
            if not root:
                clean = {k: "" for k in clean}
            live = clean["state"] not in AT_REST_STATES
            self.conn.execute(
                "INSERT INTO peers VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ref) DO UPDATE SET "
                "folder=excluded.folder,root=excluded.root,card=excluded.card,seen_at=excluded.seen_at,live=excluded.live",
                (
                    ref,
                    computer,
                    key,
                    folder,
                    root,
                    json.dumps(clean),
                    now(),
                    live,
                ),
            )

    def peer(self, ref: str, *, live: bool = False) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT p.*,c.name AS computer_name,c.seen_at AS computer_seen,c.revoked "
            "FROM peers p JOIN computers c ON c.id=p.computer WHERE p.ref=?",
            (ref,),
        ).fetchone()
        if row is None or row["revoked"] or not row["root"] or (live and not row["live"]):
            raise APIError(404, "Session is not available in this workspace")
        return dict(row)

    def allowed(
        self, first: str, second: str, *, live: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        source, target = self.peer(first, live=live), self.peer(second, live=live)
        if source["root"] != target["root"]:
            raise APIError(404, "Session is outside the permitted folder tree")
        return source, target

    def public_card(self, row: dict[str, Any]) -> dict[str, Any]:
        connected = now() - row["computer_seen"] < LEASE_MS
        card = json.loads(row["card"])
        card.pop("local_folder", None)
        card.pop("local_root", None)
        ancestry = self.ancestry(row["folder"])
        return {
            **card,
            "session_id": row["ref"],
            "session_ref": row["ref"],
            "computer_id": row["computer"],
            "computer_name": row["computer_name"],
            "folder": self.path(row["folder"]),
            "folder_id": row["folder"],
            "root_id": row["root"],
            "ancestry": ancestry[: ancestry.index(row["root"]) + 1],
            "updated_at": row["seen_at"],
            "connection": "connected" if connected else "disconnected",
            "delivery": (
                "unavailable" if not row["live"] else "available" if connected else "will_queue"
            ),
            "freshness": "current" if connected else "stale",
            "capabilities": ["discovery", "direct_messages"],
        }

    def sweep(self) -> None:
        for q in self.conn.execute(
            "SELECT * FROM questions WHERE status IN ('queued','accepted')"
        ).fetchall():
            status = None
            try:
                source, target = self.allowed(q["sender"], q["recipient"])
                if source["root"] != q["root"] or not target["live"]:
                    status = "blocked"
            except APIError:
                status = "blocked"
            if q["expires_at"] and now() >= q["expires_at"]:
                status = "expired"
            if status:
                self.conn.execute(
                    "UPDATE questions SET status=?,revision=revision+1 WHERE id=?",
                    (status, q["id"]),
                )

    def question(self, actor: str, qid: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        if row is None or actor not in (row["sender"], row["recipient"]):
            raise APIError(404, "Question not found")
        source, target = self.allowed(row["sender"], row["recipient"])
        if source["root"] != row["root"]:
            raise APIError(404, "Question no longer shared")
        q = dict(row)
        outcome = "delivered" if q["delivered"] else "waiting_for_computer"
        if q["read_at"]:
            outcome = "read"
        q.update(
            kind="question",
            sender_kind="session",
            priority=False,
            sender_name=json.loads(source["card"])["name"],
            recipient_name=json.loads(target["card"])["name"],
            requires_reply=q["status"] in ("queued", "accepted"),
            delivery={
                "outcome": outcome,
                "last_read_at": q["read_at"],
            },
        )
        return q

    def operation(self, computer: str, op: dict[str, Any]) -> dict[str, Any]:
        opid = _text(op.get("id"), "operation id", 128)
        digest = hashlib.sha256(json.dumps(op, sort_keys=True).encode()).hexdigest()
        previous = self.conn.execute(
            "SELECT * FROM operations WHERE computer=? AND id=?", (computer, opid)
        ).fetchone()
        if previous:
            if previous["digest"] != digest:
                raise APIError(409, "Operation id already used for different content")
            return cast(dict[str, Any], json.loads(previous["result"]))
        try:
            value = self._operate(computer, op)
            result = {"ok": True, "value": value}
        except APIError as exc:
            result = {"ok": False, "status": exc.status, "error": str(exc)}
        self.conn.execute(
            "INSERT INTO operations VALUES (?,?,?,?)", (computer, opid, digest, json.dumps(result))
        )
        return result

    def _operate(self, computer: str, op: dict[str, Any]) -> dict[str, Any]:
        if op.get("action") == "binding_ack":
            old = folder_path(op.get("old_path"))
            new = folder_path(op.get("new_path"))
            binding = self.conn.execute(
                "SELECT folder FROM bindings WHERE computer=? AND local_path=?", (computer, old)
            ).fetchone()
            if not binding or binding[0] != op.get("folder_id") or self.path(binding[0]) != new:
                raise APIError(409, "Folder synchronization changed; refresh the workspace")
            self.conn.execute(
                "DELETE FROM bindings WHERE computer=? AND local_path=?", (computer, old)
            )
            self.conn.execute(
                "INSERT INTO bindings VALUES (?,?,?) ON CONFLICT(computer,local_path) "
                "DO UPDATE SET folder=excluded.folder",
                (computer, new, binding[0]),
            )
            return {"updated": True}
        actor = reference(computer, op.get("actor"))
        action = op.get("action")
        if action == "ask":
            target = str(op.get("target", ""))
            split_reference(target)
            source, _ = self.allowed(actor, target, live=True)
            if actor == target:
                raise APIError(400, "Cannot ask your own session")
            text = _text(op.get("question"), "question", 16384)
            timeout = op.get("timeout_seconds", 0)
            if type(timeout) is not int or not 0 <= timeout <= 604800:
                raise APIError(400, "Invalid timeout")
            qid = _text(op.get("question_id"), "question id", 40)
            if not re.fullmatch(r"fq-[a-f0-9]{32}", qid):
                raise APIError(400, "Invalid question id")
            if self.conn.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone():
                raise APIError(409, "Question id already exists")
            pending = self.conn.execute(
                "SELECT count(*) FROM questions WHERE (sender=? OR recipient=?) "
                "AND status IN ('queued','accepted')",
                (actor, target),
            ).fetchone()[0]
            recent = self.conn.execute(
                "SELECT count(*) FROM questions WHERE sender=? AND created_at>?",
                (actor, now() - 60_000),
            ).fetchone()[0]
            if pending >= MAX_PENDING or recent >= 10:
                raise APIError(429, "Question limit reached; try again later")
            self.conn.execute(
                "INSERT INTO questions(id,sender,recipient,root,question,status,"
                "created_at,expires_at) "
                "VALUES (?,?,?,?,?,'queued',?,?)",
                (
                    qid,
                    actor,
                    target,
                    source["root"],
                    text,
                    now(),
                    now() + timeout * 1000 if timeout else 0,
                ),
            )
            return self.question(actor, qid)
        qid = _text(op.get("question_id"), "question id", 40)
        q = self.question(actor, qid)
        if action in ("receipt", "read"):
            if actor != q["recipient"]:
                raise APIError(404, "Question not found")
            self.conn.execute(
                "UPDATE questions SET delivered=1,read_at=CASE WHEN ? THEN ? ELSE read_at END "
                "WHERE id=?",
                (action == "read", now(), qid),
            )
            return {"id": qid, "delivered": True, "read": action == "read"}
        statuses = {
            "accept": "accepted",
            "answer": "answered",
            "decline": "declined",
            "cancel": "cancelled",
        }
        if action not in statuses:
            raise APIError(400, "Unsupported collaboration operation")
        if actor != (q["sender"] if action == "cancel" else q["recipient"]):
            raise APIError(404, "Question not found")
        answer = _text(op.get("text"), "text", 262144) if action in ("answer", "decline") else None
        status = statuses[action]
        if q["status"] == status and q["answer"] == answer:
            return {"id": qid, "status": status, "revision": q["revision"]}
        if q["status"] not in ("queued", "accepted") or op.get("revision") != q["revision"]:
            raise APIError(409, "Question changed; read its current state before retrying")
        self.conn.execute(
            "UPDATE questions SET status=?,answer=?,answered_at=?,revision=revision+1 WHERE id=?",
            (status, answer, None if action == "accept" else now(), qid),
        )
        return {"id": qid, "status": status, "revision": q["revision"] + 1}

    def exchange(self, token: str, request: dict[str, Any]) -> dict[str, Any]:
        computer = self.authenticate(token)
        if request.get("protocol") != PROTOCOL or request.get("workspace_id") != self.setting(
            "workspace"
        ):
            raise APIError(409, "Incompatible collaboration workspace or protocol")
        if request.get("incarnation") != computer["incarnation"]:
            raise APIError(409, "Computer identity requires owner recovery")
        writer = identifier(request.get("writer"), "writer")
        if computer["writer"] != writer and computer["lease_until"] > now():
            raise APIError(
                409, "Another active service owns this computer identity; owner recovery required"
            )
        ops = request.get("operations", [])
        if (
            not isinstance(ops, list)
            or len(ops) > MAX_BATCH
            or len(json.dumps(request).encode()) > MAX_BYTES
        ):
            raise APIError(413, "Collaboration exchange exceeds its limit")
        for op in ops:
            if not isinstance(op, dict):
                raise APIError(400, "Invalid collaboration operation")
            _text(op.get("id"), "operation id", 128)
        cursor = request.get("cursor", 0)
        if type(cursor) is not int or cursor < 0:
            raise APIError(400, "Invalid collaboration cursor")
        with self.conn:
            self.conn.execute(
                "UPDATE computers SET writer=?,lease_until=?,seen_at=? WHERE id=?",
                (writer, now() + LEASE_MS, now(), computer["id"]),
            )
            policy_ops = [op for op in ops if op.get("action") == "binding_ack"]
            message_ops = [op for op in ops if op.get("action") != "binding_ack"]
            results = {op["id"]: self.operation(computer["id"], op) for op in policy_ops}
            self.advertise(computer["id"], request.get("cards", []))
            self.sweep()
            policy = hashlib.sha256(
                json.dumps(
                    [
                        tuple(r)
                        for r in self.conn.execute(
                            "SELECT ref,folder,root,live FROM peers ORDER BY ref"
                        )
                    ]
                ).encode()
            ).hexdigest()
            if cursor and request.get("policy") != policy:
                raise APIError(409, "Folder access changed during synchronization; retry")
            results.update({op["id"]: self.operation(computer["id"], op) for op in message_ops})
            own = self.conn.execute(
                "SELECT * FROM peers WHERE computer=? AND root IS NOT NULL", (computer["id"],)
            ).fetchall()
            roots = {row["root"] for row in own}
            cards = []
            for row in self.conn.execute("SELECT ref FROM peers ORDER BY ref"):
                try:
                    peer = self.peer(row[0])
                    if peer["root"] in roots:
                        cards.append(self.public_card(peer))
                except APIError:
                    pass
            if len(json.dumps(cards).encode()) > MAX_BYTES:
                raise APIError(413, "Shared directory is too large; reduce connected session count")
            questions: list[dict[str, Any]] = []
            cursor_out = None
            used = 0
            refs = {row["ref"] for row in own}
            # Page the complete authorized inbox snapshot. Clients only replace
            # their visible cache after every page succeeds; reads require sync.
            rows = self.conn.execute(
                "SELECT sequence,id,sender,recipient FROM questions WHERE sequence>? "
                "AND (sender LIKE ? OR recipient LIKE ?) ORDER BY sequence",
                (cursor, f"peer:{computer['id']}:%", f"peer:{computer['id']}:%"),
            )
            scanned = cursor
            for row in rows:
                actor = row["sender"] if row["sender"] in refs else row["recipient"]
                if actor not in refs:
                    scanned = row["sequence"]
                    continue
                try:
                    q = self.question(actor, row["id"])
                except APIError:
                    scanned = row["sequence"]
                    continue
                size = len(json.dumps(q).encode())
                if questions and used + size > MAX_BYTES:
                    cursor_out = scanned
                    break
                questions.append(q)
                used += size
                scanned = row["sequence"]
            return {
                "protocol": PROTOCOL,
                "workspace_id": self.setting("workspace"),
                "policy": policy,
                "results": results,
                "cards": cards,
                "folder_updates": [
                    {
                        "local_path": r["local_path"],
                        "path": self.path(r["folder"]),
                        "folder_id": r["folder"],
                    }
                    for r in self.conn.execute(
                        "SELECT * FROM bindings WHERE computer=?", (computer["id"],)
                    )
                    if r["local_path"] != self.path(r["folder"])
                ],
                "questions": questions,
                "next_cursor": cursor_out,
                "synced_at": now(),
            }
