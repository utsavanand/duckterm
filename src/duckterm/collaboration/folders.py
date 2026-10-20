"""Plan recoverable sidebar renames without merging unrelated local folders."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError

if TYPE_CHECKING:
    from duckterm.persistence.history import HistoryStore


def within(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent + "/")


class FolderConflict(APIError):
    def __init__(self, path: str) -> None:
        super().__init__(
            409, "Shared folder conflicts with a local folder; resolve it before syncing"
        )
        self.path = path


def renamed(path: str, old: str, new: str) -> str:
    return new + path[len(old) :] if within(path, old) else path


def snapshot(history: HistoryStore) -> dict[str, Any]:
    conn = history.session_api.conn
    return {
        "folders": sorted(r[0] for r in conn.execute("SELECT name FROM folders")),
        "sessions": {
            r[0]: r[1] or "" for r in conn.execute("SELECT session_key,grp FROM sessions")
        },
        "grants": {
            r[0]: [r[1], r[2]]
            for r in conn.execute("SELECT session_key,root,root_mode FROM session_api_members")
        },
    }


def paths(state: dict[str, Any]) -> set[str]:
    result = set(state["folders"]) | (set(state["sessions"].values()) - {""})
    for path in list(result):
        while "/" in path:
            path = path.rpartition("/")[0]
            result.add(path)
    return result


def after_move(state: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    sessions = {
        k: (renamed(v, old, new) if new else ("" if within(v, old) else v))
        for k, v in state["sessions"].items()
    }
    grants = {}
    for key, (root, mode) in state["grants"].items():
        root = renamed(root, old, new) if new else root
        if mode == "automatic" or not root or not within(sessions[key], root):
            root, mode = sessions[key].split("/")[0], "automatic"
        grants[key] = [root, mode]
    return {
        "folders": sorted(
            renamed(p, old, new) for p in state["folders"] if new or not within(p, old)
        ),
        "sessions": sessions,
        "grants": grants,
    }


def plan_moves(state: dict[str, Any], updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preflight the entire plan; no prefix rename may absorb an existing folder.

    Move final ancestors first, then rewrite the remaining source paths. Thus A→B and
    A/child→B/renamed becomes A→B, B/child→B/renamed, not a skipped child move.
    Destinations occupied by unrelated local folders require owner recovery.
    """
    pending = sorted(
        [[u["local_path"], u["path"]] for u in updates],
        key=lambda p: (not bool(p[1]), p[1].count("/")),
    )
    steps = []
    journal_bytes = 0
    while pending:
        existing = paths(state)
        for index, (old, new) in enumerate(pending):
            if old == new:
                pending.pop(index)
                break
            if not old:
                pending.pop(index)
                break
            if new and any(other and other != new and within(new, other) for _, other in pending):
                continue
            if new in existing or within(new, old):
                continue
            pending.pop(index)
            if old in existing:
                updated = after_move(state, old, new)
                step = {"old": old, "new": new, "before": state, "after": updated}
                journal_bytes += len(json.dumps(step).encode())
                if journal_bytes > 10 * 1024 * 1024:
                    raise APIError(413, "Folder recovery journal exceeds its limit")
                steps.append(step)
                state = updated
            for remaining in pending:
                if within(remaining[0], old):
                    remaining[0] = renamed(remaining[0], old, new) if new else ""
            break
        else:
            # A name swap has no free destination. Stage one source in the
            # durable plan; none of these temporary paths are advertised.
            sources = {p[0] for p in pending}
            if all(any(within(new, source) for source in sources) for _, new in pending):
                old = pending[0][0]
                new = "Collaboration staging " + uuid.uuid4().hex
                pending.insert(0, [old, new])
                continue
            conflict = next(
                new for _, new in pending if not any(within(new, source) for source in sources)
            )
            for step in reversed(steps):
                if step["new"]:
                    conflict = renamed(conflict, step["new"], step["old"])
            raise FolderConflict(conflict)
    return steps
