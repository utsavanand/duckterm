"""Spotlight: copy a session worktree's changes onto the repo's main checkout so
you can run/test the agent's work in your primary tree without merging.

Mechanism: diff the worktree's HEAD-plus-uncommitted state against the main
branch, then `git apply` that patch to the main working tree. Non-destructive to
the worktree; the main tree ends up with the changes unstaged so you can inspect,
test, then commit or `git checkout -- .` to discard.
"""

import tempfile
from pathlib import Path

from duckterm.git.worktrees import GitError
from duckterm.review import git as review_git


def _git(cwd: Path, *args: str) -> str:
    """Both checkouts' config and attributes are the session's to write, so
    git runs through review/git: no textconv, external diff, hook, filter or
    merge driver of theirs runs."""
    try:
        return review_git.run(cwd, *args, timeout=30)
    except review_git.ReviewGitError as e:
        raise GitError(f"git {' '.join(args)} failed: {e}") from e


def spotlight_to_main(repo: Path, worktree: Path) -> list[str]:
    """Apply the worktree's full diff (committed + uncommitted, vs the main
    checkout's HEAD) onto the main working tree. Returns the changed file paths.
    Returns [] when there is nothing to sync."""
    base = _git(repo, "rev-parse", "HEAD").strip()
    # Diff the worktree's working tree against the main checkout's HEAD.
    diff = _git(worktree, "diff", "--no-ext-diff", "--no-textconv", base)
    # Include files the worktree added but hasn't committed (untracked).
    untracked = _git(worktree, "ls-files", "--others", "--exclude-standard").split()

    if not diff and not untracked:
        return []

    changed: list[str] = []
    if diff:
        with tempfile.NamedTemporaryFile("w", suffix=".patch") as patch:
            patch.write(diff)
            patch.flush()
            try:
                review_git.run(repo, "apply", "--3way", patch.name, timeout=30)
            except review_git.ReviewGitError as e:
                raise GitError(f"spotlight apply failed: {e}") from e
        changed.extend(_changed_paths(diff))

    for rel in untracked:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes((worktree / rel).read_bytes())
        changed.append(rel)

    return sorted(set(changed))


def _changed_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            paths.append(line[len("+++ b/") :])
    return paths
