"""Session-owned history discovery and retrieval over existing canonical records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm import memory_index, memory_sources
from duckterm.core.saved_progress import file_version, required_context
from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import fingerprint

if TYPE_CHECKING:
    from duckterm.server import Server


class Memory:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.locks: dict[str, asyncio.Lock] = {}
        self.cache: dict[str, dict[str, dict[str, Any]]] = {}
        self.tasks: set[asyncio.Task[dict[str, Any]]] = set()
        self.db_path = Path(server.history._conn.execute("PRAGMA database_list").fetchone()[2])

    def catalog(self, key: str) -> dict[str, Any]:
        row = self.server.history.session(key)
        if row is None:
            raise APIError(404, "Session not found")
        conn = self.server.history._conn
        required = required_context(self.server, key, row)
        root = required["scope"].get("root", "")
        cwd = str(row.get("worktree_path") or row.get("cwd") or ".")
        sources: dict[str, dict[str, Any]] = {}
        retained_sources: dict[str, dict[str, Any]] = {}

        def native(value: dict[str, Any], *, current: bool = False) -> None:
            runtime, native_id = value.get("runtime"), value.get("native_id")
            if not isinstance(runtime, str) or not isinstance(native_id, str):
                return
            identity = memory_sources.identity(key, runtime, native_id)
            entry: dict[str, Any] = {
                "id": identity,
                "kind": "conversation",
                "runtime": runtime,
                "native_id": native_id,
                "cwd": value.get("cwd") or cwd,
                "title": runtime + " conversation",
                "current": current,
            }
            for field in ("snapshot", "path", "root"):
                if field in value:
                    entry[field] = value[field]
            if entry.get("root", root) != root:
                entry["unavailable"] = "Conversation was retained under another sharing scope"
            sources[identity] = entry
            if entry.get("snapshot"):
                retained_sources[identity + ":" + entry["snapshot"]] = dict(entry)

        # Oldest to newest: the latest retained boundary for each native identity
        # is searchable; exact older snapshot handles are resolved separately later.
        for cp in conn.execute(
            "SELECT record_json FROM checkpoints WHERE session_key=? ORDER BY created_at,rowid",
            (key,),
        ):
            try:
                record = json.loads(cp[0])
                conversation = record.get("conversation")
                if isinstance(conversation, dict):
                    native(conversation)
                for retained_item in record.get("memory_sources", []):
                    if isinstance(retained_item, dict):
                        native(retained_item)
                retained = record.get("memory_source")
                if isinstance(retained, dict):
                    native(retained)
            except (ValueError, TypeError, AttributeError):
                continue
        previous = self.server.history.restart_control(key).get("previous_conversation")
        if isinstance(previous, dict):
            native_id = previous.get("native_id")
            runtime = previous.get("runtime")
            if isinstance(native_id, str) and isinstance(runtime, str):
                identity = memory_sources.identity(key, runtime, native_id)
                if identity not in sources:
                    native(previous)
        current_id = self.server.history.session_id_for(key)
        if current_id:
            native(
                {"runtime": row.get("runtime"), "native_id": current_id, "cwd": cwd}, current=True
            )
        missing_identity = current_id is None

        def text_source(kind: str, identity: str, title: str, text: str, role: str) -> None:
            if text:
                source_id = fingerprint([key, kind, identity])[:32]
                records = [{"id": "0", "role": role, "text": text}]
                sources[source_id] = {
                    "id": source_id,
                    "kind": kind,
                    "title": title,
                    "version": fingerprint(records),
                    "records": records,
                }

        text_source(
            "session",
            key,
            "Current role and notes",
            json.dumps(
                {name: required[name] for name in ("name", "purpose", "intention", "notes")},
                ensure_ascii=False,
            ),
            "current_facts",
        )
        permitted = bool(root and required["scope"].get("folder") == (row.get("grp") or ""))
        if permitted:
            for task_row in conn.execute(
                "SELECT id,title,note,status FROM folder_tasks WHERE owner_session=? "
                "AND (folder=? OR substr(folder,1,length(?)+1)=?||'/') ORDER BY id",
                (key, root, root, root),
            ):
                task = dict(task_row)
                text_source("task", task["id"], task["title"], json.dumps(task), "task")
            for mail_row in conn.execute(
                "SELECT q.id,q.sender,q.recipient,q.question,q.answer,q.status "
                "FROM session_questions q WHERE (q.recipient=? OR q.sender=?) AND q.root=? "
                "AND (q.sender='owner' OR EXISTS (SELECT 1 FROM session_api_members m "
                "JOIN sessions s ON s.session_key=m.session_key "
                "WHERE m.session_key=CASE WHEN q.sender=? THEN q.recipient ELSE q.sender END "
                "AND m.root=? AND m.folder=coalesce(s.grp,''))) ORDER BY q.rowid",
                (key, key, root, key, root),
            ):
                mail = dict(mail_row)
                text_source(
                    "inbox",
                    mail["id"],
                    "Owner decision" if mail["sender"] == "owner" else "Peer message",
                    json.dumps(mail, ensure_ascii=False),
                    "owner" if mail["sender"] == "owner" else "peer",
                )
        for event in conn.execute(
            "SELECT id,event_type,payload_json FROM events WHERE session_key=? ORDER BY rowid",
            (key,),
        ):
            text_source("event", event["id"], event["event_type"], event["payload_json"], "event")
        for item in self.server.digests.items(key):
            text_source("progress", item["id"], item["bucket"], item["text"], "derived_progress")
        for artifact in conn.execute(
            "SELECT id,title,sha256,media_type FROM artifacts WHERE session_key=? ORDER BY id",
            (key,),
        ):
            source_id = fingerprint([key, "artifact", artifact["id"]])[:32]
            if not artifact["media_type"].startswith("text/"):
                sources[source_id] = {
                    "id": source_id,
                    "kind": "attachment",
                    "title": artifact["title"],
                    "version": artifact["sha256"],
                    "records": [],
                    "unprocessed_attachments": 1,
                }
                continue
            sources[source_id] = {
                "id": source_id,
                "kind": "artifact",
                "title": artifact["title"],
                "artifact_id": artifact["id"],
                "version": artifact["sha256"],
            }
        return {
            "session": key,
            "root": root,
            "folder": row.get("grp"),
            "grant": required["scope"],
            "sources": list(sources.values()),
            "missing_identity": missing_identity,
            "retained": retained_sources,
        }

    async def retain_current(self, key: str, captured: dict[str, Any]) -> dict[str, Any] | None:
        row = self.server.history.session(key)
        conv = captured["conversation"]
        # Mechanical checkpoints still save when there is no native source.
        if row is None or captured["fence"] is None or not conv.get("native_id"):
            return None
        source = {
            "id": memory_sources.identity(key, conv["runtime"], conv["native_id"]),
            "runtime": conv["runtime"],
            "native_id": conv["native_id"],
            "cwd": str(row.get("worktree_path") or row.get("cwd") or "."),
            "path": captured["fence"]["path"],
            "root": captured["facts"]["required"]["scope"].get("root", ""),
        }
        if conv["runtime"] not in {"claude-code", "codex"}:
            return None
        result = await asyncio.to_thread(memory_sources.read_native, key, source, retain=True)
        if result["fence"] != captured["fence"]["version"]:
            raise APIError(409, "Conversation changed before retention")
        return {
            **source,
            "snapshot": result["snapshot"],
            "bytes": result["bytes"],
            "coverage": result["coverage"],
            "unprocessed_attachments": result["unprocessed_attachments"],
        }

    def materialize(
        self, catalog: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        key = catalog["session"]
        cache = self.cache.setdefault(key, {})
        available, unavailable = [], []
        if catalog["missing_identity"]:
            unavailable.append(
                {"id": "current", "reason": "Current native conversation ID is unknown"}
            )
        for source in catalog["sources"]:
            try:
                if source.get("unavailable"):
                    raise APIError(409, source["unavailable"])
                if source["kind"] == "conversation":
                    path = (
                        memory_sources.directory(key) / (source["snapshot"] + ".jsonl")
                        if source.get("snapshot")
                        else memory_sources.native_path(source)
                    )
                    stamp = file_version(str(path)) if path is not None else None
                    cache_key = fingerprint([source, str(path), stamp])
                    loaded = cache.get(cache_key)
                    if loaded is None:
                        loaded = memory_sources.read_native(key, source)
                        # The index is the long-lived cache; keep only a small
                        # normalized source in RAM, never eight huge transcripts.
                        cache.clear()
                        if (
                            sum(len(r["text"].encode()) for r in loaded["records"])
                            <= 8 * 1024 * 1024
                        ):
                            cache[cache_key] = loaded
                    available.append(loaded)
                elif source["kind"] == "artifact":
                    with sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True) as conn:
                        row = conn.execute(
                            "SELECT content,sha256 FROM artifacts WHERE id=? AND session_key=?",
                            (source["artifact_id"], key),
                        ).fetchone()
                    if row is None or row[1] != source["version"]:
                        raise APIError(409, "Artifact changed or was removed")
                    raw = bytes(row[0])
                    if hashlib.sha256(raw).hexdigest() != source["version"]:
                        raise APIError(409, "Artifact checksum does not match")
                    available.append(
                        {
                            **source,
                            "records": [
                                {"id": "0", "role": "artifact", "text": raw.decode("utf-8")}
                            ],
                        }
                    )
                else:
                    available.append(source)
            except (APIError, OSError, UnicodeError) as exc:
                unavailable.append({"id": source["id"], "reason": str(exc)})
                # Search can still expose a clearly versioned saved checkpoint
                # when the live generation disappeared. Preparation remains
                # incomplete: a retained prefix cannot stand in for later work.
                if source.get("current") and not source.get("unavailable"):
                    retained = [
                        s
                        for s in catalog["retained"].values()
                        if s["id"] == source["id"] and not s.get("unavailable")
                    ]
                    if retained:
                        try:
                            loaded = memory_sources.read_native(key, retained[-1])
                            available.append({**loaded, "retained_fallback": True})
                        except (APIError, OSError, UnicodeError):
                            pass
        return available, unavailable

    async def operate(self, key: str, action: str, query: dict[str, list[str]]) -> dict[str, Any]:
        allowed = {
            "sources": set(),
            "search": {"q", "limit"},
            "read": {"source", "record", "offset", "limit"},
        }
        if (
            action not in allowed
            or set(query) - allowed[action]
            or any(len(v) != 1 for v in query.values())
        ):
            raise APIError(400, "Unknown memory operation or query parameter")
        # Keep a canceled HTTP client from releasing a per-session index lock
        # while its worker is still writing. Different sessions have separate indexes.
        task = asyncio.create_task(self._operate(key, action, query))
        self.tasks.add(task)

        def finished(done: asyncio.Task[dict[str, Any]]) -> None:
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _operate(self, key: str, action: str, query: dict[str, list[str]]) -> dict[str, Any]:
        async with self.locks.setdefault(key, asyncio.Lock()):
            catalog = self.catalog(key)
            sources, unavailable = await asyncio.to_thread(self.materialize, catalog)
            coverage = {
                "available": len(sources),
                "unavailable": unavailable,
                "complete": not unavailable,
                "scope": "own_session",
                "unprocessed_attachments": sum(
                    s.get("unprocessed_attachments", 0) for s in sources
                ),
            }
            result: dict[str, Any] = {"coverage": coverage}
            if action == "sources":
                result["sources"] = [
                    {
                        k: s[k]
                        for k in (
                            "id",
                            "kind",
                            "title",
                            "version",
                            "runtime",
                            "native_id",
                            "current",
                        )
                        if k in s
                    }
                    for s in sources
                ]
            elif action == "search":
                text = query.get("q", [""])[0].strip()
                if not text or len(text.encode()) > 1024:
                    raise APIError(400, "Search text must contain 1–1024 UTF-8 bytes")
                limit = self._number(query, "limit", 20, 1, 50)

                def search() -> list[dict[str, Any]]:
                    path = memory_sources.directory(key) / "index.sqlite3"
                    try:
                        return memory_index.query(path, sources, text, limit)
                    except sqlite3.DatabaseError:
                        # Only this derived, per-session cache is discarded.
                        path.unlink(missing_ok=True)
                        return memory_index.query(path, sources, text, limit)

                matches = await asyncio.to_thread(search)
                for found in matches:
                    found["source"] = found["source_id"] + ":" + found["version"]
                result["results"] = matches
            else:
                handle = query.get("source", [""])[0]
                match = re.fullmatch(r"([a-f0-9]{32}):([a-f0-9]{64})", handle)
                if match is None:
                    raise APIError(400, "Invalid memory source handle")
                source = next((s for s in sources if s["id"] == match[1]), None)
                if source is None or source["version"] != match[2]:
                    retained = catalog["retained"].get(handle)
                    if retained is not None and not retained.get("unavailable"):
                        source = await asyncio.to_thread(memory_sources.read_native, key, retained)
                        sources.append(source)
                    else:
                        raise APIError(409, "Memory source changed or was removed; search again")
                record_id = query.get("record", [""])[0]
                records = source["records"]
                if record_id:
                    records = [r for r in records if r["id"] == record_id]
                    if not records:
                        raise APIError(404, "Memory record is unavailable")
                text = "\n\n".join(str(r["role"]) + ": " + r["text"] for r in records)
                offset = self._number(query, "offset", 0, 0, len(text))
                limit = self._number(query, "limit", 8000, 1, 16000)
                end = min(len(text), offset + limit)
                result.update(
                    source=handle,
                    title=source["title"],
                    text=text[offset:end],
                    offset=offset,
                    next_offset=end if end < len(text) else None,
                )
            if fingerprint(catalog) != fingerprint(self.catalog(key)):
                raise APIError(409, "Memory permissions or sources changed; retry")
            # File replacement between worker completion and response must not
            # publish a stale excerpt, even when size and mtime were preserved.
            for source in sources:
                if (
                    source.get("fence") is not None
                    and file_version(source["path"]) != source["fence"]
                ):
                    raise APIError(409, "Conversation changed; retry")
            return result

    @staticmethod
    def _number(query: dict[str, list[str]], key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(query.get(key, [str(default)])[0])
        except ValueError as exc:
            raise APIError(400, "Invalid " + key) from exc
        if not low <= value <= high:
            raise APIError(400, "Invalid " + key)
        return value

    async def session_request(
        self, method: str, path: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        api = self.server.history.session_api
        key = api.authenticate(headers)
        if method != "GET":
            raise APIError(405, "Memory retrieval is read-only")
        parsed = urllib.parse.urlsplit(path)
        action = parsed.path.removeprefix("/api/v1/session/memory/")
        result = await self.operate(
            key, action, urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        )
        # Enrollment/token may have been revoked during a worker read.
        if api.authenticate(headers) != key:
            raise APIError(403, "Session access changed")
        return result
