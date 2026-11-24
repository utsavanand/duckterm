"""Convert raw conversation copies kept before records-v1 into records copies.

Owner-run, once. Each raw version a checkpoint or summary still cites is read
and verified against its native digest, saved as records (one whole copy per
growing conversation, older versions as pointers into it), read back and
compared, and then every database reference to it gains its records digest.
Only after that commit do the raw files move to a holding folder the owner
deletes once satisfied. Raw files nothing
cites are reported and left alone. A run interrupted after the commit leaves
bound versions whose raw copy is still in place; the next run checks those
again and finishes moving them.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
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


def _raw(root: Path, snapshot: str) -> list[Path]:
    return [
        root / (snapshot + suffix)
        for suffix in (".jsonl", ".prefix")
        if (root / (snapshot + suffix)).is_file()
    ]


def pending(conn: sqlite3.Connection) -> dict[str, dict[Version, str | None]]:
    """Cited versions per agent still to convert.

    None means no reference has a records digest yet. A digest means every
    reference is bound but the raw copy is still in place (an interrupted run).
    """
    unbound: dict[str, set[Version]] = defaultdict(set)
    bound: dict[str, dict[Version, str]] = defaultdict(dict)
    for _, _, session, value in _rows(conn):
        refs: list[dict[str, Any]] = []
        _walk(value, refs)
        for ref in refs:
            version = (ref["runtime"], ref["native_id"], ref["snapshot"])
            if "records_sha256" in ref:
                bound[session][version] = ref["records_sha256"]
            else:
                unbound[session].add(version)
    result: dict[str, dict[Version, str | None]] = {}
    for session in unbound.keys() | bound.keys():
        try:
            root: Path | None = memory_sources.location(session)
        except APIError:
            root = None  # Reported by plan() and refused by convert().
        versions: dict[Version, str | None] = {
            v: d
            for v, d in bound[session].items()
            if root is not None and v not in unbound[session] and _raw(root, v[2])
        }
        versions.update(dict.fromkeys(unbound[session]))
        if versions:
            result[session] = versions
    return result


def plan(conn: sqlite3.Connection) -> dict[str, Any]:
    """What a conversion would do, without changing anything."""
    sessions = []
    todo = pending(conn)
    for key, versions in sorted(todo.items()):
        try:
            root = memory_sources.location(key)
        except APIError as exc:
            sessions.append({"session": key, "versions": len(versions), "error": str(exc)})
            continue
        raw = [p for _, _, snapshot in versions for p in _raw(root, snapshot)]
        sessions.append(
            {
                "session": key,
                "versions": len(versions),
                "raw_bytes": sum(p.stat().st_size for p in raw),
            }
        )
    cited = {s for versions in todo.values() for _, _, s in versions}
    uncited = [
        p
        for p in (paths.home() / "checkpoints").glob("*/memory/*")
        if p.suffix in {".jsonl", ".prefix"} and HEX.fullmatch(p.stem) and p.stem not in cited
    ]
    return {
        "sessions": sessions,
        "raw_bytes": sum(s.get("raw_bytes", 0) for s in sessions),
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


def convert(conn: sqlite3.Connection, key: str, trash: Path, anchor: Path) -> dict[str, Any]:
    """Convert one agent's cited raw versions; the raw files go to `trash`.

    `trash` must lie under `anchor`; every folder below the anchor is opened
    without following symlinks.
    """
    root = memory_sources.directory(key)
    report: dict[str, Any] = {
        "session": key,
        "converted": 0,
        "resumed": 0,
        "unreadable": [],
        "removed": [],
    }
    # Opened before anything changes: a holding folder that can't be reached
    # (a symlink, or macOS privacy protection) must stop the run
    # here, not after every reference has been rebound.
    trash_fd = open_below(anchor, trash)
    try:
        return _convert(conn, key, root, trash_fd, report)
    finally:
        os.close(trash_fd)


def _convert(
    conn: sqlite3.Connection, key: str, root: Path, trash_fd: int, report: dict[str, Any]
) -> dict[str, Any]:
    with (root / "latest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        read: dict[Version, dict[str, Any]] = {}
        resumed: dict[Version, str] = {}
        for version, digest in sorted(pending(conn).get(key, {}).items()):
            runtime, native_id, snapshot = version
            ref = {"runtime": runtime, "native_id": native_id, "snapshot": snapshot}
            try:
                read[version] = memory_sources.read_native(key, ref)
            except APIError as exc:
                report["unreadable"].append({"snapshot": snapshot, "reason": str(exc)})
                continue
            if digest is not None:
                resumed[version] = digest
        # Longest first, so each shorter version can point into a whole copy.
        by_identity: dict[tuple[str, str], list[tuple[str, list[bytes]]]] = defaultdict(list)
        for (runtime, native_id, snapshot), value in read.items():
            if (runtime, native_id, snapshot) in resumed:
                continue
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
        report["converted"] = len(bound)
        report["resumed"] = len(resumed)
        bound.update(resumed)
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
            # A bound reference falls back to the raw copy when no records file
            # exists, which would make this comparison check nothing.
            if (
                Path(again["path"]).suffix != ".records"
                or again["records"] != read[version]["records"]
            ):
                raise APIError(409, f"Converted copy {snapshot} does not read back the same")
        with transaction(conn):
            for table, rowid, _, value in _rows(conn, key):
                if _bind(value, bound):
                    column = dict(TABLES)[table]
                    conn.execute(
                        f"UPDATE {table} SET {column}=? WHERE rowid=?", (json.dumps(value), rowid)
                    )
        index_path = root / "latest.json"
        try:
            index = json.loads(index_path.read_text())
        except (OSError, ValueError):
            index = {}
        changed = False
        # From every bound version, not only this run's, so a run interrupted
        # before this point still gets its index entries updated on re-run.
        for (runtime, native_id, snapshot), value in read.items():
            name = runtime + ":" + native_id
            entry = index.get(name)
            if (
                (runtime, native_id, snapshot) in bound
                and isinstance(entry, dict)
                and "format" not in entry
                and entry.get("digest") == snapshot
                and (root / (snapshot + ".records")).is_file()
            ):
                index[name] = {
                    "format": memory_sources.RECORDS_FORMAT,
                    "digest": snapshot,
                    "bytes": value["bytes"],
                    "records": len(value["records"]),
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
        names = [
            snapshot + suffix
            # Pointers before the copies they point into, so an interrupted move
            # never leaves a raw version that can't be read and checked again.
            for suffix in (".prefix", ".jsonl")
            for snapshot in sorted(converted - needed)
            if (root / (snapshot + suffix)).is_file()
        ]
        if names:
            report["removed"] = _move_to_trash(root, names, trash_fd, key)
    return report


def open_below(anchor: Path, target: Path) -> int:
    """Open (creating as needed) target, one folder at a time below anchor.

    O_NOFOLLOW on a whole path only checks its last component, so a symlinked
    parent would still be followed. The anchor itself is trusted as given.
    """
    if not target.is_relative_to(anchor):
        raise APIError(409, f"The holding folder must be inside {anchor}")
    fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in target.relative_to(anchor).parts:
            if part in {"", ".", ".."} or "/" in part:
                raise APIError(409, "Invalid holding folder")
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, 0o700, dir_fd=fd)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def _move_to_trash(root: Path, names: list[str], trash_fd: int, key: str) -> list[dict[str, Any]]:
    """Move files by hard link and unlink, through directories opened without
    following symlinks, so nothing in the holding folder is replaced and
    nothing lands outside it. One on another volume is refused, not copied."""
    moved = []
    fds = [os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    try:
        with contextlib.suppress(FileExistsError):
            os.mkdir(key, 0o700, dir_fd=trash_fd)
        fds.append(os.open(key, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=trash_fd))
        source, destination = fds
        for name in names:
            info = os.stat(name, dir_fd=source, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                continue
            try:
                os.link(
                    name, name, src_dir_fd=source, dst_dir_fd=destination, follow_symlinks=False
                )
            except FileExistsError as exc:
                there = os.stat(name, dir_fd=destination, follow_symlinks=False)
                # The same file is already linked there by an interrupted run.
                if (there.st_dev, there.st_ino) != (info.st_dev, info.st_ino):
                    raise APIError(
                        409, f"The holding folder already has {name}; nothing replaced"
                    ) from exc
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    raise APIError(
                        409, "The holding folder is on another volume; nothing moved"
                    ) from exc
                raise
            os.unlink(name, dir_fd=source)
            moved.append({"file": name, "bytes": info.st_size})
    finally:
        for fd in fds:
            os.close(fd)
    return moved
