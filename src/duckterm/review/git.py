"""Read-only git facts for the PR review platform.

Every call runs against a session worktree or its main repository and never
changes a working tree, index or local branch. The only ref writes are the
remote-tracking refs and tags a bounded `fetch` brings in.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


class ReviewGitError(RuntimeError):
    pass


def _env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not (k.startswith("GIT_") and k not in ("GIT_SSH", "GIT_SSH_COMMAND"))
    }
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def run(path: str | Path, *args: str, timeout: float = 15) -> str:
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewGitError(f"git {args[0]} timed out") from exc
    except OSError as exc:
        raise ReviewGitError(str(exc)) from exc
    if result.returncode:
        raise ReviewGitError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def is_repo(path: str | Path) -> bool:
    try:
        return run(path, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except ReviewGitError:
        return False


def repo_root(path: str | Path) -> str:
    """The main repository of a worktree (or the repository itself)."""
    common = run(path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return str(Path(common).resolve().parent)


def current_branch(path: str | Path) -> str | None:
    return run(path, "branch", "--show-current").strip() or None


def default_branch(repo: str | Path) -> str:
    try:
        ref = run(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").strip()
        if ref.startswith("origin/"):
            return ref[len("origin/") :]
    except ReviewGitError:
        pass
    for name in ("main", "master"):
        if ref_exists(repo, f"refs/heads/{name}") or ref_exists(
            repo, f"refs/remotes/origin/{name}"
        ):
            return name
    return "main"


def ref_exists(repo: str | Path, ref: str) -> bool:
    try:
        run(repo, "rev-parse", "--verify", "--quiet", ref)
        return True
    except ReviewGitError:
        return False


def resolve(repo: str | Path, ref: str) -> str | None:
    try:
        return run(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip() or None
    except ReviewGitError:
        return None


def base_ref(repo: str | Path, branch: str) -> str:
    """Prefer the remote-tracking ref for the base so a stale local `main`
    does not hide a conflict the remote already has."""
    remote = f"origin/{branch}"
    return remote if ref_exists(repo, f"refs/remotes/{remote}") else branch


_SLUG = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def github_slug(repo: str | Path) -> str | None:
    try:
        url = run(repo, "remote", "get-url", "origin").strip()
    except ReviewGitError:
        return None
    return slug_from_url(url)


def slug_from_url(url: str) -> str | None:
    match = _SLUG.search(url.strip())
    return f"{match[1]}/{match[2]}" if match else None


def ahead_behind(repo: str | Path, base: str, head: str) -> tuple[int, int]:
    out = run(repo, "rev-list", "--left-right", "--count", f"{base}...{head}").split()
    return int(out[1]), int(out[0])


def merge_base(repo: str | Path, base: str, head: str) -> str | None:
    try:
        return run(repo, "merge-base", base, head).strip() or None
    except ReviewGitError:
        return None


def conflicts(repo: str | Path, base: str, head: str) -> bool | None:
    """True when merging `head` into `base` would conflict, None when git
    cannot tell (missing ref, old git)."""
    if resolve(repo, base) is None or resolve(repo, head) is None:
        return None
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repo),
                "merge-tree",
                "--write-tree",
                "--name-only",
                base,
                head,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    return None


def shortstat(repo: str | Path, base: str, head: str) -> dict[str, int] | None:
    try:
        out = run(repo, "diff", "--no-ext-diff", "--shortstat", f"{base}...{head}")
    except ReviewGitError:
        return None
    files = additions = deletions = 0
    for part in out.strip().split(","):
        words = part.strip().split()
        if not words or not words[0].isdigit():
            continue
        value = int(words[0])
        if "file" in part:
            files = value
        elif "insertion" in part:
            additions = value
        elif "deletion" in part:
            deletions = value
    return {"files": files, "additions": additions, "deletions": deletions}


def commit_time(repo: str | Path, ref: str) -> int | None:
    try:
        return int(run(repo, "log", "-1", "--format=%ct", ref).strip())
    except (ReviewGitError, ValueError):
        return None


def tag_containing(repo: str | Path, sha: str) -> str | None:
    """The first release tag (creation order) that contains a commit."""
    try:
        out = run(repo, "tag", "--contains", sha, "--sort=creatordate", "--list", "v*")
    except ReviewGitError:
        return None
    tags = out.split()
    return tags[0] if tags else None


def remote_heads(repo: str | Path) -> dict[str, str] | None:
    """Branch -> tip sha on origin, from one `ls-remote` round trip. None when
    the remote is unreachable."""
    try:
        out = run(repo, "ls-remote", "--heads", "origin", timeout=20)
    except ReviewGitError:
        return None
    heads: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/") :]] = sha
    return heads


def fetch(repo: str | Path, refs: list[str], *, tags: bool = True) -> str | None:
    """Bring the named remote branches and the tags up to date. Every ref must
    exist on origin (see remote_heads). Bounded and non-fatal: returns the
    error text when the network is unavailable."""
    args = ["fetch", "--quiet", "--no-recurse-submodules"]
    if tags:
        args.append("--tags")
    args.append("origin")
    args += [f"+refs/heads/{ref}:refs/remotes/origin/{ref}" for ref in refs]
    try:
        run(repo, *args, timeout=25)
    except ReviewGitError as exc:
        return str(exc) or "fetch failed"
    return None
