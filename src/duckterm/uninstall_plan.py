"""Read-only uninstall inventory. No server, agent, config or data mutations."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from duckterm.agents import hook_files, hooks_install
from duckterm.harnesses import installable_agents
from duckterm.helpers import instance


@contextlib.contextmanager
def _directory(path: Path) -> Iterator[int | None]:
    absolute = path.absolute()
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parent.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                yield None
                return
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _owned_count(config: dict[str, Any], *, unverified: bool = False) -> int:
    def count(value: Any) -> int:
        if isinstance(value, dict):
            own = sum(
                (
                    (
                        hooks_install._hook_invocation(v) is not None
                        and not hooks_install._is_ours(v)
                    )
                    if unverified
                    else hooks_install._is_ours(v)
                )
                for k, v in value.items()
                if k in {"command", "bash", "powershell"}
            )
            return own + sum(count(v) for v in value.values() if isinstance(v, dict | list))
        if isinstance(value, list):
            return sum(count(v) for v in value)
        return 0

    return count(config.get("hooks", {}))


def inspect_hook(agent: str, *, project: Path | None = None) -> dict[str, Any]:
    _, spec = hooks_install._spec(agent)
    path = spec.path(global_scope=project is None, project_dir=project or Path.cwd())
    result: dict[str, Any] = {
        "agent": agent,
        "path": str(path),
        "scope": "global" if project is None else "project",
        "state": "absent",
        "owned_commands": 0,
    }
    try:
        with _directory(path) as fd:
            if fd is None:
                return result
            if spec.transport == "file-drop":
                raw = hook_files._owned(path.name, "." + path.name + ".duckterm-owned.json", fd)
                if raw is not None:
                    result.update(state="removable", owned_commands=1)
                return result
            raw = hook_files._read(path.name, fd)
            if raw is None:
                return result
        config = json.loads(raw)
        if not isinstance(config, dict) or spec.strip is None:
            raise ValueError("Unsupported hook configuration")
        candidate = spec.strip(copy.deepcopy(config))
        count = _owned_count(config) - _owned_count(candidate)
        ambiguous = _owned_count(candidate, unverified=True)
        result.update(state="removable" if count else "unchanged", owned_commands=count)
        if ambiguous:
            result.update(
                state="blocked",
                unverified_commands=ambiguous,
                reason="Custom or historical hook paths require manual ownership review",
            )
    except (OSError, ValueError, TypeError):
        # Never include source text or parser excerpts: config can contain secrets.
        result.update(
            state="blocked",
            owned_commands=None,
            reason="Ownership or configuration could not be verified; inspect manually",
        )
    return result


def footprint(root: Path, *, limit: int = 200000) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(root),
        "bytes": 0,
        "entries": 0,
        "complete": True,
        "action": "keep",
    }
    if not root.exists() and not root.is_symlink():
        return result
    if root.is_symlink() or not root.is_dir():
        return {**result, "complete": False, "reason": "Data root is not a regular directory"}

    def failed(error: OSError) -> None:
        result["complete"] = False

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        for name in dirs + files:
            if result["entries"] >= limit:
                return {**result, "complete": False, "reason": "Inventory entry limit reached"}
            result["entries"] += 1
            try:
                info = (Path(directory) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    result["bytes"] += info.st_size
            except OSError:
                result["complete"] = False
    return result


def inventory(data_root: Path, projects: list[Path]) -> dict[str, Any]:
    paths = list(dict.fromkeys(p.absolute() for p in projects))
    hooks = [inspect_hook(agent) for agent in installable_agents()]
    hooks.extend(inspect_hook(agent, project=p) for p in paths for agent in installable_agents())
    profile = instance.configured_profile()
    manual_steps = ["pipx uninstall duckterm", "Move DuckTerm.app to Trash"]
    if profile is not None:
        manual_steps = [
            f"Quit {profile['app_name']} and move only {profile['app_name']}.app to Trash",
            f"Keep {data_root} unless you explicitly choose to delete this workspace",
            "Keep the shared CLI, shared hook dispatcher, and other editions' apps and data",
        ]
    return {
        "dry_run": True,
        "hooks": hooks,
        "data": footprint(data_root),
        "project_scope": [str(p) for p in paths],
        "limitations": [
            "Only global hooks and explicitly listed project directories were inspected.",
            "Connector cleanup and process shutdown must be verified before package removal.",
            "No files were changed and no processes were stopped.",
        ],
        "remaining_manual_steps_after_cleanup": manual_steps,
    }
