"""Bounded, read-only conversation discovery. Paths never come from HTTP input."""

import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from duckterm.runtimes.claude_code import project_slug

WINDOW = 256 * 1024
MAX_ENTRIES = 2000
MAX_CANDIDATES = 40
MAX_SCAN_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024


def excerpts(
    path: Path,
    runtime: str,
    cwd: str,
    verify_digest: bool = True,
    *,
    expected_native_id: str | None = None,
) -> dict[str, Any] | None:
    # Refuse symlinks at every component, including renamed roots. O_NOFOLLOW
    # closes the final-component race between the resolution and open checks.
    if path.resolve() != path.absolute():
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                return None
            head = stream.read(WINDOW)
            stream.seek(max(0, before.st_size - WINDOW))
            tail = stream.read(WINDOW)
            digest = hashlib.sha256()
            available = not verify_digest or before.st_size <= MAX_FILE_BYTES
            reason = (
                None
                if available
                else "Transcript exceeds the 32 MiB limit. Recover it in the original harness."
            )
            if available and verify_digest:
                stream.seek(0)
                deadline = time.monotonic() + 4
                remaining = MAX_FILE_BYTES + 1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
                    if time.monotonic() > deadline:
                        available, reason = (
                            False,
                            "Verification timed out. Try again on the original computer.",
                        )
                        break
                if remaining == 0:
                    available, reason = (
                        False,
                        "Transcript grew beyond the verification limit. Try again.",
                    )
            after = os.fstat(stream.fileno())

        def stamp(s: os.stat_result) -> tuple[int, ...]:
            return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

        if stamp(before) != stamp(after) or stamp(after) != stamp(path.stat()):
            return None
        if path.resolve() != path.absolute():
            return None
    except OSError:
        return None
    first_lines = head.splitlines()
    last_lines = tail.splitlines()
    if before.st_size > WINDOW:
        first_lines = first_lines[:-1]
        last_lines = last_lines[1:]
    objects: list[dict[str, Any]] = []
    for line in first_lines + last_lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                objects.append(value)
        except (ValueError, UnicodeError):
            continue
    if runtime == "codex":
        meta = objects[0] if objects else {}
        payload = meta.get("payload")
        if meta.get("type") != "session_meta" or not isinstance(payload, dict):
            return None
        native_id = payload.get("id")
        recorded_cwd = payload.get("cwd")
        if not isinstance(native_id, str) or not path.name.endswith(f"-{native_id}.jsonl"):
            return None
    else:
        native_id = path.stem
        recorded_cwd = next((obj.get("cwd") for obj in objects if obj.get("cwd")), None)
        if not any(obj.get("sessionId") == native_id for obj in objects) or any(
            obj.get("sessionId") not in (None, native_id) for obj in objects
        ):
            return None
    from duckterm.persistence.native_identity import valid

    if not valid(native_id) or not isinstance(recorded_cwd, str):
        return None
    if expected_native_id is not None and native_id != expected_native_id:
        return None
    if (
        runtime == "claude-code"
        and expected_native_id is not None
        and not any(
            isinstance(obj.get("message"), dict)
            and obj["message"].get("role") in {"user", "assistant"}
            and isinstance(obj["message"].get("content"), str | list)
            and obj["message"]["content"]
            for obj in objects
        )
    ):
        return None
    if not Path(recorded_cwd).is_absolute():
        return None
    if (
        runtime == "claude-code"
        and expected_native_id is not None
        and project_slug(Path(recorded_cwd)) != path.parent.name
    ):
        return None
    # Only an already-recorded exact Claude identity may cross project
    # directories. Discovery/adoption keeps its original cwd restriction.
    if str(Path(recorded_cwd).resolve()) != cwd and (
        runtime != "claude-code" or expected_native_id != native_id
    ):
        return None

    def prompts(lines: list[bytes]) -> list[str]:
        found = []
        for line in lines:
            try:
                obj = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(obj, dict):
                continue
            if runtime == "codex":
                if obj.get("type") != "response_item":
                    continue
                message = obj.get("payload")
            else:
                message = obj.get("message")
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    str(block["text"])
                    for block in content
                    if isinstance(block, dict)
                    and block.get("type") in {"text", "input_text"}
                    and isinstance(block.get("text"), str)
                )
            if isinstance(content, str) and content.strip():
                found.append(content.strip()[:1600])
        return found

    first, last = prompts(first_lines), prompts(last_lines)
    return {
        "native_id": native_id,
        "path": str(path),
        "fingerprint": [*stamp(after), digest.hexdigest()],
        "available": available,
        "reason": reason,
        "firstPrompt": first[0] if first else None,
        "lastPrompt": last[-1] if last else None,
        "modifiedAt": after.st_mtime_ns // 1_000_000,
    }


def candidates(runtime: str, cwd: str) -> tuple[list[dict[str, Any]], bool]:
    root = Path.home() / (".claude/projects" if runtime == "claude-code" else ".codex/sessions")
    if runtime == "claude-code":
        root /= project_slug(Path(cwd))
    if not root.exists():
        return [], False
    if root.resolve() != root.absolute():
        return [], False
    result: list[dict[str, Any]] = []
    visited = 0
    read_budget = MAX_SCAN_BYTES
    # Sorted names give a stable order, never a newest/recommended selection.
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        with os.scandir(directory) as entries:
            batch = []
            for entry in entries:
                visited += 1
                if visited > MAX_ENTRIES:
                    return result, True
                batch.append(entry)
        for entry in sorted(batch, key=lambda value: value.name):
            if runtime == "codex" and entry.is_dir(follow_symlinks=False) and depth < 3:
                stack.append((Path(entry.path), depth + 1))
            elif entry.name.endswith(".jsonl") and entry.is_file(follow_symlinks=False):
                cost = min(entry.stat(follow_symlinks=False).st_size, MAX_FILE_BYTES) + WINDOW * 2
                if cost > read_budget:
                    return result, True
                read_budget -= cost
                value = excerpts(Path(entry.path), runtime, cwd)
                if value:
                    result.append(value)
                    if len(result) >= MAX_CANDIDATES:
                        return result, True
    return result, False
