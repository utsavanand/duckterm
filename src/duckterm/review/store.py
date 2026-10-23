"""On-disk review state under ~/.duckterm/review/<repo>/<ref>/: viewed hashes,
bucket overrides and the heads the owner has looked at. JSON caches that can
be lost without harm (spec §2)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from duckterm.helpers import paths

VIEWED = "viewed.json"


def repo_key(repo: str, slug: str | None) -> str:
    if slug:
        return slug.replace("/", "__")
    return f"{Path(repo).name}-{hashlib.sha1(repo.encode()).hexdigest()[:10]}"


def ref_key(ref: str) -> str:
    if ref.isdigit():
        return f"pr-{ref}"
    return "branch-" + re.sub(r"[^A-Za-z0-9._-]+", "-", ref).strip("-")


def review_dir(repo: str, slug: str | None, ref: str) -> Path:
    return paths.home() / "review" / repo_key(repo, slug) / ref_key(ref)


def load(directory: Path, name: str) -> dict[str, Any]:
    path = directory / name
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def save(directory: Path, name: str, value: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{name}.tmp"
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(directory / name)


def record_head(directory: Path, head: str) -> list[dict[str, Any]]:
    """Remember a head the owner has opened. Returns the history, newest last."""
    state = load(directory, VIEWED)
    heads: list[dict[str, Any]] = list(state.get("heads") or [])
    if not heads or heads[-1].get("sha") != head:
        heads.append({"sha": head, "at": int(time.time())})
        state["heads"] = heads[-50:]
        save(directory, VIEWED, state)
    return heads


def previous_head(heads: list[dict[str, Any]], head: str) -> str | None:
    """The last head the owner looked at before this one."""
    for entry in reversed(heads):
        if entry.get("sha") != head:
            return str(entry["sha"])
    return None


def mark(
    directory: Path, path: str, hash_: str, *, viewed: bool | None, bucket: str | None
) -> dict[str, Any]:
    state = load(directory, VIEWED)
    files = state.setdefault("files", {})
    entry = dict(files.get(path) or {})
    if viewed is not None:
        entry.update({"hash": hash_, "viewed": viewed, "at": int(time.time())})
    if bucket is not None:
        if bucket == "":
            entry.pop("bucket", None)
        else:
            entry["bucket"] = bucket
    files[path] = entry
    save(directory, VIEWED, state)
    return entry
