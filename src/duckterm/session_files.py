"""Owner-only, bounded directory pages rooted in a recorded local session."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import itertools
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError
from duckterm.helpers import security
from duckterm.persistence.artifacts import MAX_FILE_BYTES, MEDIA_TYPES
from duckterm.transport.httpio import write_json

if TYPE_CHECKING:
    from duckterm.server import Server

PAGE_SIZE = 200
MAX_ENTRIES = 10000


def relative_path(value: str) -> str:
    try:
        size = len(value.encode())
    except UnicodeError as exc:
        raise APIError(400, "Path must be valid Unicode") from exc
    if (
        size > 4096
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or any(p.casefold() in {"..", ".git"} for p in value.split("/"))
    ):
        raise APIError(400, "Choose a relative path inside the session folder")
    return str(Path(value)) if value else "."


@contextlib.contextmanager
def directory(root: Path, relative: str) -> Iterator[int]:
    """Pin each directory component; never follow a swapped-in symbolic link."""
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (*root.parts[1:], *Path(relative).parts):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def stamp(root: Path, relative: str, info: os.stat_result) -> str:
    value = (str(root), relative, info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
    return hashlib.sha256(repr(value).encode()).hexdigest()


def ignored(root: Path, names: list[str]) -> set[str]:
    """Let installed Git interpret nested patterns, negation and tracked files."""
    if not names:
        return set()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]

    def run(args: list[str], data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            command + args, cwd=root, env=env, input=data, capture_output=True, timeout=5
        )

    data = b"\x00".join(os.fsencode(name) for name in names) + b"\x00"
    try:
        result = run(["check-ignore", "-z", "--stdin"], data)
        if result.returncode not in (0, 1):
            # A plain directory can have .gitignore rules too. Use an empty
            # temporary Git index; never initialize or write to the user's root.
            probe = run(["rev-parse", "--is-inside-work-tree"])
            if probe.returncode == 0:
                raise APIError(503, "Unable to evaluate this folder's ignore rules")
            with tempfile.TemporaryDirectory(prefix="duckterm-file-ignore-") as temporary:
                gitdir = Path(temporary)
                (gitdir / "objects").mkdir()
                (gitdir / "refs").mkdir()
                (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
                result = run(
                    [
                        "--git-dir=" + temporary,
                        "--work-tree=" + str(root),
                        "-c",
                        "core.bare=false",
                        "check-ignore",
                        "--no-index",
                        "-z",
                        "--stdin",
                    ],
                    data,
                )
        if result.returncode not in (0, 1):
            raise APIError(503, "Unable to evaluate this folder's ignore rules")
    except (OSError, subprocess.SubprocessError) as exc:
        raise APIError(503, "Git is unavailable or ignore checking timed out") from exc
    return {os.fsdecode(value) for value in result.stdout.split(b"\x00") if value}


def registrations(
    root: Path, recorded_root: str, artifacts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Existence is checked only inside this root; saved bytes remain separate."""
    result = []
    for artifact in artifacts:
        path = None
        source = Path(artifact["source_path"])
        for base in (Path(recorded_root), root):
            if source.is_relative_to(base):
                path = str(source.relative_to(base))
                break
        state = "outside_root"
        if path is not None:
            try:
                safe = relative_path(path)
                with directory(root, str(Path(safe).parent)) as descriptor:
                    info = os.stat(Path(safe).name, dir_fd=descriptor, follow_symlinks=False)
                state = "present" if stat.S_ISREG(info.st_mode) else "unavailable"
            except FileNotFoundError:
                state = "missing"
            except (OSError, APIError):
                state = "unavailable"
        result.append(
            {**artifact, "path": path, "source_state": state, "content_only": state != "present"}
        )
    return result


def listing(
    recorded_root: str, relative: str, cursor: str, artifacts: list[dict[str, Any]]
) -> dict[str, Any]:
    root = Path(recorded_root).resolve(strict=True)
    with directory(root, relative) as descriptor:
        info = os.fstat(descriptor)
        revision = stamp(root, relative, info)
        offset = 0
        if cursor:
            match = re.fullmatch(r"([a-f0-9]{64}):(\d{1,5})", cursor)
            if not match or not 0 < int(match[2]) < MAX_ENTRIES or int(match[2]) % PAGE_SIZE:
                raise APIError(400, "Invalid directory cursor")
            if match[1] != revision:
                raise APIError(409, "Directory changed; refresh its listing")
            offset = int(match[2])
        rows: list[dict[str, Any]] = []
        with os.scandir(descriptor) as iterator:
            page = list(
                itertools.islice(iterator, offset, min(offset + PAGE_SIZE, MAX_ENTRIES) + 1)
            )
        more = len(page) > PAGE_SIZE
        for entry in page[:PAGE_SIZE]:
            if entry.name.casefold() == ".git":
                continue
            relative_name = str(Path(relative) / entry.name)
            try:
                details = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(details.st_mode):
                kind = "directory"
            elif stat.S_ISREG(details.st_mode):
                kind = "file"
            elif stat.S_ISLNK(details.st_mode):
                kind = "symlink"
            else:
                kind = "other"
            rows.append(
                {
                    "name": entry.name,
                    "path": relative_name,
                    "type": kind,
                    "size": details.st_size if kind == "file" else None,
                    "modified_at": details.st_mtime_ns // 1000000,
                    "browsable": kind == "directory"
                    or (kind == "file" and details.st_size <= MAX_FILE_BYTES),
                    "content_limit": MAX_FILE_BYTES,
                }
            )
        names = [r["path"] + ("/" if r["type"] == "directory" else "") for r in rows]
        hidden = ignored(root, names + ([relative + "/"] if relative != "." else []))
        if relative + "/" in hidden:
            raise APIError(403, "This directory is excluded by ignore rules")
        rows = [r for r, name in zip(rows, names, strict=True) if name not in hidden]
        saved = registrations(root, recorded_root, artifacts)
        by_path = {a["path"]: a for a in saved if a["source_state"] == "present"}
        for row in rows:
            row["artifact"] = by_path.get(row["path"])
        if (
            stamp(root, relative, os.fstat(descriptor)) != revision
            or stamp(root, relative, (root / relative).stat()) != revision
        ):
            raise APIError(409, "Directory changed; refresh its listing")
    next_offset = offset + PAGE_SIZE
    limited = more and next_offset >= MAX_ENTRIES
    return {
        "available": True,
        "root": str(root),
        "path": relative,
        "entries": sorted(rows, key=lambda r: (r["type"] != "directory", r["name"].casefold())),
        "next_cursor": f"{revision}:{next_offset}" if more and not limited else None,
        "truncated": more,
        "limit_reached": limited,
        "ignore_status": "applied",
        "registration_scope": "selected_session",
        "artifacts": saved,
    }


def content(recorded_root: str, relative: str) -> dict[str, Any]:
    root = Path(recorded_root).resolve(strict=True)
    target = Path(relative)
    if not target.name:
        raise APIError(400, "Choose a file inside the session folder")
    with directory(root, str(target.parent)) as descriptor:
        folder_stamp = stamp(root, str(target.parent), os.fstat(descriptor))
        file = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise APIError(400, "Only regular files can be previewed or downloaded")
            if relative in ignored(root, [relative]):
                raise APIError(403, "This file is excluded by ignore rules")
            if before.st_size > MAX_FILE_BYTES:
                return {
                    "available": False,
                    "reason": "file_too_large",
                    "size": before.st_size,
                    "content_limit": MAX_FILE_BYTES,
                    "path": relative,
                    "name": target.name,
                }
            data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = os.stat(target.name, dir_fd=descriptor, follow_symlinks=False)

        def version(info: os.stat_result) -> tuple[int, ...]:
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

        if (
            version(before) != version(after)
            or version(after) != version(current)
            or len(data) != after.st_size
            or len(data) > MAX_FILE_BYTES
            or stamp(root, str(target.parent), (root / target.parent).stat()) != folder_stamp
        ):
            raise APIError(409, "File changed while reading; try again")
    media = MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        if media.startswith("text/") or media == "image/svg+xml":
            media = "application/octet-stream"
    else:
        if media == "application/octet-stream" and "\x00" not in text:
            media = "text/plain"
    return {
        "available": True,
        "path": relative,
        "name": target.name,
        "size": len(data),
        "modified_at": after.st_mtime_ns // 1000000,
        "media_type": media,
        "content_base64": base64.b64encode(data).decode("ascii"),
        "content_limit": MAX_FILE_BYTES,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


async def handle(
    server: Server,
    writer: asyncio.StreamWriter,
    headers: dict[str, str],
    key: str,
    method: str,
    operation: str,
    query: dict[str, list[str]],
) -> None:
    try:
        if not security.token_valid(headers, server.token):
            raise APIError(401, "Owner credential required")
        if method != "GET":
            raise APIError(405, "File browsing is read-only")
        if key.startswith("~remote~"):
            await write_json(writer, 200, {"available": False, "reason": "remote_not_browsable"})
            return
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key):
            raise APIError(400, "Invalid session identity")
        allowed = {"path", "cursor"} if operation == "files" else {"path"}
        if set(query) - allowed or any(len(v) != 1 for v in query.values()):
            raise APIError(400, "Expected one relative path and optional cursor")
        relative = relative_path(query.get("path", [""])[0])
        cursor = query.get("cursor", [""])[0]
        row = server.history.session(key)
        if row is None:
            raise APIError(404, "Session is unavailable on this host")
        root = row.get("worktree_path") or row.get("cwd")
        artifacts = (
            server.history.artifacts.list(key)
            if operation == "files" and server.edition.includes("artifacts")
            else []
        )
        if not isinstance(root, str) or "\x00" in root or not Path(root).is_absolute():
            await write_json(
                writer,
                200,
                {
                    "available": False,
                    "reason": "directory_not_recorded",
                    "artifacts": [
                        {**a, "source_state": "unavailable", "content_only": True, "path": None}
                        for a in artifacts
                    ],
                },
            )
            return
        try:
            if operation == "files":
                result = await asyncio.to_thread(listing, root, relative, cursor, artifacts)
            else:
                result = await asyncio.to_thread(content, root, relative)
        except FileNotFoundError:
            result = {
                "available": False,
                "reason": "directory_missing" if operation == "files" else "file_missing",
            }
        except OSError:
            result = {
                "available": False,
                "reason": "directory_unreadable" if operation == "files" else "file_unreadable",
            }
        if not result.get("available"):
            result["artifacts"] = [
                {**a, "source_state": "unavailable", "content_only": True, "path": None}
                for a in artifacts
            ]
        current = server.history.session(key)
        if current is None or (current.get("worktree_path") or current.get("cwd")) != root:
            raise APIError(409, "Session directory changed; refresh its listing")
        await write_json(writer, 200, result)
    except APIError as exc:
        await write_json(writer, exc.status, {"error": str(exc)})
