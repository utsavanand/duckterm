"""Parse `git diff` output into files and hunks, and hash each file's hunks so
viewed state survives pushes that leave the file's change untouched."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
MAX_LINES = 2000
HEAD_LINES = 400


@dataclass
class Hunk:
    header: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    status: str = "modified"
    binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for kind, _ in h.lines if kind == "+")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for kind, _ in h.lines if kind == "-")

    @property
    def added_lines(self) -> list[str]:
        return [text for h in self.hunks for kind, text in h.lines if kind == "+"]

    @property
    def removed_lines(self) -> list[str]:
        return [text for h in self.hunks for kind, text in h.lines if kind == "-"]


def _strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix) :] if value.startswith(prefix) else value


def _header_paths(line: str) -> tuple[str, str]:
    body = line[len("diff --git ") :]
    # "a/x b/x": paths with spaces are ambiguous here; the ---/+++ and rename
    # lines that follow refine them.
    if body.startswith('"'):
        return body, body
    half = (len(body) - 1) // 2
    left, right = body[:half], body[half + 1 :]
    return _strip_prefix(left, "a/"), _strip_prefix(right, "b/")


def parse(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            _, new = _header_paths(raw)
            current = FileDiff(path=new, old_path=None)
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if hunk is not None and raw[:1] in (" ", "+", "-", "\\"):
            hunk.lines.append((raw[:1], raw[1:]))
            continue
        if raw.startswith("new file mode"):
            current.status = "added"
        elif raw.startswith("deleted file mode"):
            current.status = "deleted"
        elif raw.startswith("rename from "):
            current.old_path = raw[len("rename from ") :]
            current.status = "renamed"
        elif raw.startswith("rename to "):
            current.path = raw[len("rename to ") :]
        elif raw.startswith("copy from "):
            current.old_path = raw[len("copy from ") :]
            current.status = "copied"
        elif raw.startswith("copy to "):
            current.path = raw[len("copy to ") :]
        elif raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            current.binary = True
        elif raw.startswith("--- ") and not raw.startswith("--- /dev/null"):
            if current.status not in ("renamed", "copied"):
                current.old_path = None
            if current.status == "modified":
                current.path = _strip_prefix(raw[4:], "a/")
        elif raw.startswith("+++ ") and not raw.startswith("+++ /dev/null"):
            if current.status in ("modified", "added"):
                current.path = _strip_prefix(raw[4:], "b/")
        else:
            match = HUNK.match(raw)
            if match:
                hunk = Hunk(
                    header=match[5],
                    old_start=int(match[1]),
                    old_lines=int(match[2] or 1),
                    new_start=int(match[3]),
                    new_lines=int(match[4] or 1),
                )
                current.hunks.append(hunk)
    return files


_WS = re.compile(r"\s+")


def hunk_hash(file: FileDiff) -> str:
    """SHA-1 of the file's hunk lines with all whitespace removed. Computed on
    the whitespace-insensitive diff, so a reformat keeps a file viewed and a
    content change clears it."""
    digest = hashlib.sha1()
    for hunk in file.hunks:
        for kind, text in hunk.lines:
            if kind == "\\":
                continue
            digest.update(kind.encode())
            digest.update(_WS.sub("", text).encode("utf-8", "replace"))
    return digest.hexdigest()


def to_json(file: FileDiff) -> dict[str, Any]:
    total = sum(len(h.lines) for h in file.hunks)
    truncated = total > MAX_LINES
    hunks: list[dict[str, Any]] = []
    budget = HEAD_LINES if truncated else total
    for hunk in file.hunks:
        if budget <= 0:
            break
        lines = hunk.lines[:budget]
        budget -= len(lines)
        hunks.append(
            {
                "header": hunk.header,
                "old_start": hunk.old_start,
                "old_lines": hunk.old_lines,
                "new_start": hunk.new_start,
                "new_lines": hunk.new_lines,
                "lines": [{"kind": kind, "text": text} for kind, text in lines],
            }
        )
    return {
        "path": file.path,
        "old_path": file.old_path,
        "status": file.status,
        "binary": file.binary,
        "additions": file.additions,
        "deletions": file.deletions,
        "hunks": hunks,
        "truncated": truncated,
        "total_lines": total,
    }
