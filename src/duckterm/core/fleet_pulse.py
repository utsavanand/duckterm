"""The Fleet monitor's Pulse: the few fleet events worth the owner's attention.

Only records the server already has. Needs-you items come from relay notes in
the browser; this module adds artifacts made, PRs merged and releases tagged.
PR merges and releases are read from the local clones sessions work in (merge
commits and v* tags on the default branch), so nothing calls GitHub and a
squash-merged PR doesn't appear. Turn ends and state changes are deliberately
left out (owner, 2026-11-24: too frequent to be a pulse).
"""

import re
from pathlib import Path
from typing import Any

from duckterm.review import git
from duckterm.review.history import _merges

WINDOW_MS = 7 * 86_400_000
LIMIT = 60
ARTIFACT_BURST_MS = 3_600_000
_MERGE = re.compile(r"Merge pull request #(\d+) from [^/\s]+/(\S+)")


def repo_events(repo: str, since_ms: int) -> list[dict[str, Any]]:
    """PR merges and release tags in one clone since since_ms, newest first."""
    try:
        ref = git.run(repo, "rev-parse", "--abbrev-ref", "origin/HEAD").strip() or "HEAD"
    except git.ReviewGitError:
        ref = "HEAD"
    name = Path(repo).name
    events: list[dict[str, Any]] = []
    try:
        log = git.run(
            repo,
            "log",
            ref,
            "--merges",
            "--first-parent",
            f"--since=@{since_ms // 1000}",
            "--max-count=50",
            "--format=%ct%x1f%s%x1f%b%x1e",
        )
    except git.ReviewGitError:
        log = ""
    for record in log.split("\x1e"):
        parts = record.strip().split("\x1f")
        if len(parts) != 3:
            continue
        when, subject, body = parts
        match = _MERGE.match(subject)
        if not match:
            continue
        title = body.strip().splitlines()[0] if body.strip() else subject
        events.append(
            {
                "kind": "pr",
                "at": int(when) * 1000,
                "title": f"#{match[1]} {title}"[:160],
                "repo": name,
                "branch": match[2],
            }
        )
    # Newest tags first, stopping at the window: a repo can have hundreds.
    try:
        tags = git.run(
            repo,
            "tag",
            "--list",
            "v*",
            "--sort=-creatordate",
            "--format=%(refname:short)%00%(creatordate:unix)",
        ).splitlines()
    except git.ReviewGitError:
        tags = []
    for i, line in enumerate(tags):
        tag, _, when = line.partition("\x00")
        at = int(when or 0) * 1000
        if at < since_ms:
            break
        previous = tags[i + 1].partition("\x00")[0] if i + 1 < len(tags) else None
        merged = len(_merges(repo, previous, tag))
        events.append(
            {
                "kind": "release",
                "at": at,
                "title": tag,
                "repo": name,
                "detail": f"{merged} merged PR{'' if merged == 1 else 's'}" if merged else "",
            }
        )
    return events


def clones(sessions: list[dict[str, Any]], since_ms: int) -> dict[str, list[dict[str, Any]]]:
    """Each repository's main clone, with the recent sessions working in it.

    Worktrees of one repository share a common git dir, so it is read once."""
    found: dict[str, list[dict[str, Any]]] = {}
    roots: dict[str, str | None] = {}
    for row in sessions:
        path = row.get("worktree_path") or row.get("repo_path") or row.get("cwd")
        if not path or int(row.get("updated_at") or 0) < since_ms:
            continue
        if path not in roots:
            try:
                common = git.run(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
                roots[path] = str(Path(common.strip()).parent)
            except git.ReviewGitError:
                roots[path] = None
        root = roots[path]
        if root is not None:
            found.setdefault(root, []).append(row)
    return found


def collect(
    sessions: list[dict[str, Any]], artifacts: list[dict[str, Any]], now_ms: int
) -> list[dict[str, Any]]:
    since = now_ms - WINDOW_MS
    events: list[dict[str, Any]] = []
    # One session's artifacts within an hour are one event: a burst of
    # preview screenshots is a single thing that happened.
    for a in sorted(artifacts, key=lambda a: a["created_at"], reverse=True):
        last = next((e for e in reversed(events) if e["session_key"] == a["session_key"]), None)
        if last is not None and last["first"] - a["created_at"] <= ARTIFACT_BURST_MS:
            last["count"] += 1
            last["first"] = a["created_at"]
            continue
        events.append(
            {
                "kind": "artifact",
                "at": a["created_at"],
                "first": a["created_at"],
                "count": 1,
                "title": a["title"],
                "session_key": a["session_key"],
                "name": a["session_name"],
            }
        )
    for e in events:
        del e["first"]
    for repo, members in clones(sessions, since).items():
        by_branch = {str(m.get("branch") or ""): m for m in members}
        for event in repo_events(repo, since):
            # A merged branch that a session worked on names that session.
            owner = by_branch.get(event.pop("branch", ""))
            if owner is not None:
                event["session_key"] = owner["session_key"]
                event["name"] = owner.get("name") or owner["session_key"]
            events.append(event)
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:LIMIT]
