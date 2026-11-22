"""Convert raw conversation copies kept before records-v1 into records copies.

Owner-run, once. Each raw version a checkpoint or summary still cites is read
and verified against its native digest, saved as records (one whole copy per
growing conversation, older versions as pointers into it), read back and
compared, and then every database reference to it gains its records digest.
Only after that commit do the raw files move to the Trash. Raw files nothing
cites are reported and left alone.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from duckterm import memory_sources
from duckterm.core.session_api import APIError
from duckterm.helpers import paths
from duckterm.helpers.private_files import private_write
from duckterm.persistence.saved_state import transaction

# Every place a saved conversation reference lives (checked against the real
# database, 2026-11-22): summary revisions and links, and checkpoint records.
TABLES = (("checkpoints", "record_json"), ("digest_items", "text"))
HEX = re.compile(r"[a-f0-9]{64}")

Version = tuple[str, str, str]  # runtime, native_id, snapshot


def _walk(value: Any, found: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if (
            isinstance(value.get("runtime"), str)
            and isinstance(value.get("native_id"), str)
            and isinstance(value.get("snapshot"), str)
            and HEX.fullmatch(value["snapshot"])
        ):
            found.append(value)
        for item in value.values():
            _walk(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk(item, found)


def _rows(conn: sqlite3.Connection, key: str | None = None) -> list[tuple[str, int, str, Any]]:
    result = []
    for table, column in TABLES:
        query = f"SELECT rowid, session_key, {column} FROM {table} WHERE {column} LIKE ?"
        args: list[Any] = ['%"snapshot"%']
        if key is not None:
            query += " AND session_key=?"
            args.append(key)
        for rowid, session, text in conn.execute(query, args).fetchall():
            try:
                result.append((table, rowid, session, json.loads(text)))
            except ValueError:
                continue
    return result


def pending(conn: sqlite3.Connection) -> dict[str, set[Version]]:
    """Referenced versions per agent that have no records digest yet."""
    result: dict[str, set[Version]] = defaultdict(set)
    for _, _, session, value in _rows(conn):
        refs: list[dict[str, Any]] = []
        _walk(value, refs)
        for ref in refs:
            if "records_sha256" not in ref:
                result[session].add((ref["runtime"], ref["native_id"], ref["snapshot"]))
    return result


def plan(conn: sqlite3.Connection) -> dict[str, Any]:
    """What a conversion would do, without changing anything."""
    sessions = []
    for key, versions in sorted(pending(conn).items()):
        root = memory_sources.directory(key)
        raw = [
            root / (snapshot + suffix)
            for _, _, snapshot in versions
            for suffix in (".jsonl", ".prefix")
            if (root / (snapshot + suffix)).is_file()
        ]
        sessions.append(
            {
                "session": key,
                "versions": len(versions),
                "raw_bytes": sum(p.stat().st_size for p in raw),
            }
        )
    cited = {s for versions in pending(conn).values() for _, _, s in versions}
    uncited = [
        p
        for p in (paths.home() / "checkpoints").glob("*/memory/*")
        if p.suffix in {".jsonl", ".prefix"} and HEX.fullmatch(p.stem) and p.stem not in cited
    ]
    return {
        "sessions": sessions,
        "raw_bytes": sum(s["raw_bytes"] for s in sessions),
        "uncited_raw_files": len(uncited),
        "uncited_raw_bytes": sum(p.stat().st_size for p in uncited),
    }


def _digest(lines: list[bytes]) -> str:
    return hashlib.sha256(b"".join(lines)).hexdigest()


def _bind(value: Any, bound: dict[Version, str]) -> bool:
    refs: list[dict[str, Any]] = []
    _walk(value, refs)
    changed = False
    for ref in refs:
        digest = bound.get((ref["runtime"], ref["native_id"], ref["snapshot"]))
        if digest is not None and "records_sha256" not in ref:
            ref["records_sha256"] = digest
            changed = True
    return changed


def _still_needed(root: Path, converted: set[str]) -> set[str]:
    """Raw files that an unconverted pointer still resolves through."""
    needed: set[str] = set()
    for pointer in root.glob("*.prefix"):
        stem = pointer.name.split(".")[0]
        if stem in converted:
            continue
        seen = {stem}
        while True:
            try:
                target = json.loads((root / (stem + ".prefix")).read_text())["of"]
            except (OSError, ValueError, KeyError, TypeError):
                break
            if not isinstance(target, str) or target in seen:
                break
            seen.add(target)
            needed.add(target)
            stem = target
    return needed


def convert(conn: sqlite3.Connection, key: str, trash: Path) -> dict[str, Any]:
    """Convert one agent's cited raw versions; the raw files go to `trash`."""
    root = memory_sources.directory(key)
    report: dict[str, Any] = {"session": key, "converted": 0, "unreadable": [], "trashed": []}
    with (root / "latest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        read: dict[Version, dict[str, Any]] = {}
        for version in sorted(pending(conn).get(key, ())):
            runtime, native_id, snapshot = version
            ref = {"runtime": runtime, "native_id": native_id, "snapshot": snapshot}
            try:
                read[version] = memory_sources.read_native(key, ref)
            except APIError as exc:
                report["unreadable"].append({"snapshot": snapshot, "reason": str(exc)})
        # Longest first, so each shorter version can point into a whole copy.
        by_identity: dict[tuple[str, str], list[tuple[str, list[bytes]]]] = defaultdict(list)
        for (runtime, native_id, snapshot), value in read.items():
            lines = [memory_sources.record_line(r) for r in value["records"]]
            by_identity[(runtime, native_id)].append((snapshot, lines))
        bound: dict[Version, str] = {}
        wholes: dict[tuple[str, str], list[tuple[str, list[bytes]]]] = defaultdict(list)
        for identity, versions in by_identity.items():
            versions.sort(key=lambda v: len(v[1]), reverse=True)
            for snapshot, lines in versions:
                value = read[(*identity, snapshot)]
                digest = _digest(lines)
                whole = next(
                    (
                        name
                        for name, kept in wholes[identity]
                        if len(kept) >= len(lines) and _digest(kept[: len(lines)]) == digest
                    ),
                    None,
                )
                if whole is None:
                    if not (root / (snapshot + ".records")).exists():
                        memory_sources.write_records(
                            root, snapshot, value["bytes"], value["unprocessed_attachments"], lines
                        )
                    wholes[identity].append((snapshot, lines))
                elif not (root / (snapshot + ".records")).exists():
                    pointer = {
                        "of": whole,
                        "records": len(lines),
                        "sha256": digest,
                        "bytes": value["bytes"],
                        "unprocessed_attachments": value["unprocessed_attachments"],
                    }
                    private_write(root / (snapshot + ".records-prefix"), json.dumps(pointer))
                bound[(*identity, snapshot)] = digest
        # Nothing is rebound or moved unless every version reads back the same.
        for version, digest in bound.items():
            runtime, native_id, snapshot = version
            again = memory_sources.read_native(
                key,
                {
                    "runtime": runtime,
                    "native_id": native_id,
                    "snapshot": snapshot,
                    "records_sha256": digest,
                },
            )
            if again["records"] != read[version]["records"]:
                raise APIError(409, f"Converted copy {snapshot} does not read back the same")
        with transaction(conn):
            for table, rowid, _, value in _rows(conn, key):
                if _bind(value, bound):
                    column = dict(TABLES)[table]
                    conn.execute(
                        f"UPDATE {table} SET {column}=? WHERE rowid=?", (json.dumps(value), rowid)
                    )
        report["converted"] = len(bound)
        index_path = root / "latest.json"
        try:
            index = json.loads(index_path.read_text())
        except (OSError, ValueError):
            index = {}
        changed = False
        for (runtime, native_id), kept in wholes.items():
            name = runtime + ":" + native_id
            entry = index.get(name)
            names = {snapshot for snapshot, _ in kept}
            if isinstance(entry, dict) and "format" not in entry and entry.get("digest") in names:
                lines = dict(kept)[entry["digest"]]
                value = read[(runtime, native_id, entry["digest"])]
                index[name] = {
                    "format": memory_sources.RECORDS_FORMAT,
                    "digest": entry["digest"],
                    "bytes": value["bytes"],
                    "records": len(lines),
                    "unprocessed_attachments": value["unprocessed_attachments"],
                }
                changed = True
        if changed:
            fd, name = tempfile.mkstemp(prefix=".latest-", dir=root)
            with os.fdopen(fd, "w") as stream:
                json.dump(index, stream)
            os.replace(name, index_path)
        converted = {snapshot for _, _, snapshot in bound}
        needed = _still_needed(root, converted)
        for snapshot in sorted(converted - needed):
            for suffix in (".jsonl", ".prefix"):
                path = root / (snapshot + suffix)
                if path.is_file() and not path.is_symlink():
                    destination = trash / key / path.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    size = path.stat().st_size
                    shutil.move(path, destination)
                    report["trashed"].append({"file": path.name, "bytes": size})
    return report
