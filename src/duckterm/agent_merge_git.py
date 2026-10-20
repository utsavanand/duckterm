"""Read-only worktree evidence for an owner-reviewed integration request.

The recipient agent performs the integration; this module never changes refs,
indexes or files in either running agent's worktree.
"""

import os
import subprocess
from pathlib import Path
from typing import Any


def _git(path: str, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", path, *args],
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )
    if result.returncode:
        raise ValueError("Git could not verify these worktrees")
    return result.stdout.strip()


def snapshot(source: str | None, target: str | None) -> dict[str, Any]:
    if not source or not target:
        return {"allowed": False, "reason": "Both agents need a Git project folder."}
    try:
        left = _git(source, "rev-parse", "--show-toplevel")
        right = _git(target, "rev-parse", "--show-toplevel")
        if Path(left).resolve() == Path(right).resolve():
            return {"allowed": False, "reason": "These agents already share the same worktree."}
        common = _git(left, "rev-parse", "--path-format=absolute", "--git-common-dir")
        other = _git(right, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if Path(common).resolve() != Path(other).resolve():
            return {
                "allowed": False,
                "reason": "The worktrees must belong to the same repository on this computer.",
            }
        source_head = _git(left, "rev-parse", "HEAD")
        target_head = _git(right, "rev-parse", "HEAD")
        base = _git(right, "merge-base", target_head, source_head)
        dirty = bool(_git(left, "status", "--porcelain", "--untracked-files=normal"))
        target_dirty = bool(_git(right, "status", "--porcelain", "--untracked-files=normal"))
        count = int(_git(right, "rev-list", "--count", f"{target_head}..{source_head}"))
        reason = None
        if dirty:
            reason = (
                "Commit the source worktree's edits before including code. "
                "Uncommitted edits are not transferred."
            )
        elif target_dirty:
            reason = "Commit or stash the destination worktree's edits before including code."
        elif count == 0:
            reason = "The destination already contains all source commits."
        return {
            "allowed": reason is None,
            "reason": reason,
            "sourcePath": left,
            "targetPath": right,
            "sourceCommit": source_head,
            "targetCommit": target_head,
            "sourceBranch": _git(left, "branch", "--show-current") or "Detached HEAD",
            "targetBranch": _git(right, "branch", "--show-current") or "Detached HEAD",
            "commits": count,
            "changes": _git(
                right,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--stat",
                "--stat-count=20",
                base,
                source_head,
            ),
        }
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {
            "allowed": False,
            "reason": "Could not verify both worktrees. Context and notes can still be merged.",
        }
