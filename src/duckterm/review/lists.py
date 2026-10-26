"""Pull request rows for a session or a folder.

A row is a branch a session's worktree is on, joined with the pull request
whose head is that branch. A folder lists only its own sessions' branches,
never other pull requests in the same repository (owner ruling, 2026-10-26).
Git facts come from the local repository; PR facts come from GitHub and are
cached per repository for CACHE_SECONDS, so a list that polls every minute
costs one page of pulls plus one check-runs and one reviews call per open PR.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from duckterm.persistence.folder_chats import within
from duckterm.review import git, github, status

CACHE_SECONDS = 60

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


def _iso_to_epoch(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _repo_state(repo: str, branches: list[str], *, refresh: bool) -> dict[str, Any]:
    """GitHub pulls and remote heads for one repository, refreshed at most
    once per CACHE_SECONDS unless the owner asked."""
    now = time.time()
    with _lock:
        entry = _cache.get(repo)
        fresh = entry is not None and now - entry["at"] < CACHE_SECONDS
        if entry is not None and not refresh and fresh:
            return entry
    slug = git.github_slug(repo)
    default = git.default_branch(repo)
    heads = git.remote_heads(repo)
    fetch_error = None
    if heads is not None:
        wanted = [b for b in dict.fromkeys([default, *branches]) if b in heads]
        fetch_error = git.fetch(repo, wanted)
    pulls: list[dict[str, Any]] = []
    error: str | None = None
    token: str | None = None
    if slug:
        token, error = github.token()
        if token:
            try:
                pulls = github.pulls(slug, token)
            except github.GitHubError as exc:
                error = str(exc)
    elif heads is None:
        error = "The remote is unreachable"
    entry = {
        "at": time.time(),
        "slug": slug,
        "default": default,
        "heads": heads,
        "fetch_error": fetch_error,
        "pulls": pulls,
        "error": error,
        "token": token,
        "checks": {},
        "reviews": {},
    }
    with _lock:
        _cache[repo] = entry
    return entry


def _checks(state: dict[str, Any], sha: str) -> dict[str, int] | None:
    if not state["token"] or not state["slug"]:
        return None
    if sha not in state["checks"]:
        try:
            state["checks"][sha] = github.check_runs(state["slug"], sha, state["token"])
        except github.GitHubError as exc:
            state["error"] = state["error"] or str(exc)
            return None
    value: dict[str, int] | None = state["checks"][sha]
    return value


def _review(state: dict[str, Any], number: int) -> str:
    if not state["token"] or not state["slug"]:
        return "none"
    if number not in state["reviews"]:
        try:
            state["reviews"][number] = github.review_state(state["slug"], number, state["token"])
        except github.GitHubError as exc:
            state["error"] = state["error"] or str(exc)
            return "none"
    value: str = state["reviews"][number]
    return value


def _pull_for(pulls: list[dict[str, Any]], branch: str) -> dict[str, Any] | None:
    matches = [p for p in pulls if p["head"] == branch]
    if not matches:
        return None
    open_ones = [p for p in matches if p["state"] == "open"]
    return (open_ones or matches)[0]


def _row(
    repo: str,
    state: dict[str, Any],
    *,
    branch: str,
    pr: dict[str, Any] | None,
    session: dict[str, str] | None,
    local: bool,
) -> dict[str, Any] | None:
    default = state["default"]
    base = pr["base"] if pr else default
    base_ref = git.base_ref(repo, base)
    heads = state["heads"]
    # Offline, the last fetch's remote-tracking ref is the best evidence of a push.
    pushed = (
        branch in heads
        if heads is not None
        else git.ref_exists(repo, f"refs/remotes/origin/{branch}")
    )
    head_ref = branch if local and git.ref_exists(repo, f"refs/heads/{branch}") else None
    if head_ref is None and pushed and git.ref_exists(repo, f"refs/remotes/origin/{branch}"):
        head_ref = f"origin/{branch}"
    ahead = behind = None
    size = None
    conflict = None
    tip_time = None
    if head_ref and git.ref_exists(repo, base_ref):
        ahead, behind = git.ahead_behind(repo, base_ref, head_ref)
        size = git.shortstat(repo, base_ref, head_ref)
        tip_time = git.commit_time(repo, head_ref)
        if pr is None or pr["state"] == "open":
            conflict = git.conflicts(repo, base_ref, head_ref) if ahead else False
    if pr is None and not pushed and not ahead:
        return None
    unpushed = 0
    if head_ref and pushed and git.ref_exists(repo, f"refs/remotes/origin/{branch}"):
        unpushed, _ = git.ahead_behind(repo, f"origin/{branch}", head_ref)
    checks = _checks(state, pr["head_sha"]) if pr and pr["state"] == "open" else None
    review = _review(state, pr["number"]) if pr and pr["state"] == "open" else "none"
    version = None
    if pr and pr["state"] == "merged" and pr.get("merge_commit_sha"):
        version = git.tag_containing(repo, str(pr["merge_commit_sha"]))
    verdict = status.derive(
        pr, pushed=pushed, conflict=conflict, checks=checks, review=review, version=version
    )
    slug = state["slug"]
    compare_url = (
        f"https://github.com/{slug}/compare/{base}...{branch}?expand=1"
        if slug and pr is None and pushed
        else None
    )
    updated = _iso_to_epoch(pr["updated_at"]) if pr else None
    return {
        "repo": repo,
        "repo_name": Path(repo).name,
        "github": slug,
        "branch": branch,
        "base": base,
        "session": session,
        "author": pr["author"] if pr else None,
        "pr": pr,
        "status": verdict["status"],
        "label": verdict["label"],
        "needs_you": verdict["needs_you"],
        "version": version,
        "checks": checks,
        "review": review,
        "conflict": conflict,
        "pushed": pushed,
        "unpushed_commits": unpushed,
        "ahead": ahead,
        "behind": behind,
        "size": size,
        "updated_at": updated or tip_time,
        "compare_url": compare_url,
    }


def _worktree_branch(row: dict[str, Any]) -> tuple[str, str] | None:
    """(repository root, branch) for a session that works in a git checkout."""
    path = row.get("worktree_path") or row.get("cwd")
    if not path or not Path(str(path)).is_dir() or not git.is_repo(str(path)):
        return None
    try:
        repo = git.repo_root(str(path))
        branch = git.current_branch(str(path)) or str(row.get("branch") or "")
    except git.ReviewGitError:
        return None
    return (repo, branch) if branch else None


def build(sessions: list[dict[str, Any]], *, refresh: bool = False) -> dict[str, Any]:
    by_repo: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for row in sessions:
        found = _worktree_branch(row)
        if found:
            by_repo.setdefault(found[0], []).append((row, found[1]))
    rows: list[dict[str, Any]] = []
    repos: list[dict[str, Any]] = []
    for repo, members in by_repo.items():
        state = _repo_state(repo, [b for _, b in members], refresh=refresh)
        for session_row, branch in members:
            if branch == state["default"]:
                continue
            pr = _pull_for(state["pulls"], branch)
            session = {
                "key": str(session_row["session_key"]),
                "name": str(session_row.get("name") or session_row["session_key"]),
            }
            built = _row(repo, state, branch=branch, pr=pr, session=session, local=True)
            if built:
                rows.append(built)
        repos.append(
            {
                "path": repo,
                "name": Path(repo).name,
                "github": state["slug"],
                "default_branch": state["default"],
                "connected": bool(state["token"]),
                "error": state["error"] or state["fetch_error"],
                "fetched_at": int(state["at"]),
            }
        )
    rows.sort(key=lambda r: (not r["needs_you"], -(r["updated_at"] or 0)))
    return {"rows": rows, "repos": repos}


def folder_members(history: Any, folder: str) -> list[dict[str, Any]]:
    """Session rows in a folder and its subfolders. Runs on the server's loop
    thread: the SQLite connection must not be touched from `build`'s thread."""
    return [row for row in history.sessions() if within(str(row.get("grp") or ""), folder)]
