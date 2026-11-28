"""The GitHub issues trigger: which open issues should start a run.

Polled by the server (spec: every 2 minutes; a webhook needs a public
endpoint). An issue starts a run once. The first poll after a flow is put on
autopilot only records what is already there, so turning a flow on never
starts runs for old issues. Changing the labels or the issue repository keeps
that record; the person editing chooses whether open issues that now match
should run (`waiting`, `relabel`).

Only people with write access start runs: with labels, whoever added the
matching label; without labels, the issue's author. Issues that fail that
check are looked at again next time, so a maintainer can label one later.
"""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

WRITE = frozenset({"admin", "maintain", "write"})
Get = Callable[[str], Any]


def api(token: str) -> Get:
    def get(path: str) -> Any:
        req = urllib.request.Request(
            "https://api.github.com" + path,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "duckterm-autopilot",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    return get


def poll(
    get: Get, repo: str, labels: list[str], seen_path: Path, limit: int
) -> list[dict[str, Any]]:
    """Up to `limit` issues that should start a run now, each recorded as
    started. The rest wait for a later poll."""
    state = _read(seen_path)
    found = _open_issues(get, repo, labels)
    # No record for this repository: an edit writes one (relabel), so this is
    # a first poll, or a seen file from before issue repositories existed.
    if not state or state.get("repo", repo) != repo:
        _write(seen_path, {"repo": repo, "labels": labels, "started": sorted(found)})
        return []
    started = set(state.get("started") or [])
    ready: list[dict[str, Any]] = []
    for number, issue in sorted(found.items()):
        if len(ready) >= limit:
            break
        if number in started:
            continue
        who = _starter(get, repo, issue, labels)
        if who and _can_write(get, repo, who):
            ready.append(_summary(issue, who))
            started.add(number)
    if ready or state.get("labels") != labels:
        _write(seen_path, {**state, "repo": repo, "labels": labels, "started": sorted(started)})
    return ready


def waiting(get: Get, repo: str, labels: list[str], seen_path: Path) -> list[int]:
    """Open issues in repo with any of labels that have not started a run:
    what watching them would pick up, before the labeller check."""
    state = _read(seen_path)
    started = set(state.get("started") or []) if state.get("repo", repo) == repo else set()
    return sorted(set(_open_issues(get, repo, labels)) - started)


def relabel(seen_path: Path, repo: str, labels: list[str], skip: list[int], answer: str) -> None:
    """Record a change of labels or issue repository. Issues already started
    stay started; those in skip are marked started too, so only issues
    labelled from now on run. answer is what the person chose, kept for the
    record."""
    state = _read(seen_path)
    started = set(state.get("started") or []) if state.get("repo", repo) == repo else set()
    _write(
        seen_path,
        {
            "repo": repo,
            "labels": labels,
            "started": sorted(started | set(skip)),
            "on_change": answer,
        },
    )


def check_access(get: Get, repo: str, issue_repo: str) -> None:
    """Raises ValueError unless the token can push to repo and read issues in
    issue_repo, so a setup that cannot work is refused when it is saved."""
    code = get(f"/repos/{repo}")
    if not code:
        raise ValueError(f"The GitHub token can't reach {repo}")
    if not (code.get("permissions") or {}).get("push"):
        raise ValueError(f"The GitHub token can't push to {repo}")
    if issue_repo != repo and not get(f"/repos/{issue_repo}"):
        raise ValueError(f"The GitHub token can't reach {issue_repo}")


def labelled(get: Get, repo: str, labels: list[str]) -> set[int]:
    """Open issues that carry any of the labels now."""
    return set(_open_issues(get, repo, labels))


def issue(get: Get, repo: str, number: int) -> dict[str, Any]:
    """One issue, for a run started by hand from the app."""
    found = get(f"/repos/{repo}/issues/{number}")
    if not found or "pull_request" in found:
        raise ValueError(f"{repo} has no issue #{number}")
    return _summary(found, None)


def _open_issues(get: Get, repo: str, labels: list[str]) -> dict[int, dict[str, Any]]:
    found: dict[int, dict[str, Any]] = {}
    # GitHub's `labels` filter means all of them; a flow means any of them.
    for label in labels or [""]:
        query = {"state": "open", "per_page": "50", "sort": "created", "direction": "desc"}
        if label:
            query["labels"] = label
        for item in get(f"/repos/{repo}/issues?{urllib.parse.urlencode(query)}") or []:
            if "pull_request" not in item:
                found[int(item["number"])] = item
    return found


def _starter(get: Get, repo: str, issue: dict[str, Any], labels: list[str]) -> str | None:
    if not labels:
        return str((issue.get("user") or {}).get("login") or "") or None
    events = get(f"/repos/{repo}/issues/{issue['number']}/events?per_page=100") or []
    for event in reversed(events):
        if event.get("event") == "labeled" and (event.get("label") or {}).get("name") in labels:
            return str((event.get("actor") or {}).get("login") or "") or None
    return None


def _can_write(get: Get, repo: str, login: str) -> bool:
    found = get(f"/repos/{repo}/collaborators/{urllib.parse.quote(login)}/permission")
    return bool(found) and found.get("permission") in WRITE


def _summary(issue: dict[str, Any], started_by: str | None) -> dict[str, Any]:
    return {
        "number": int(issue["number"]),
        "title": str(issue.get("title") or ""),
        "body": str(issue.get("body") or ""),
        "labels": [str(lb.get("name")) for lb in issue.get("labels") or []],
        "url": str(issue.get("html_url") or ""),
        "author": str((issue.get("user") or {}).get("login") or ""),
        "started_by": started_by,
    }


def _read(path: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(path.read_text()))
    except FileNotFoundError:
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data) + "\n")
    tmp.replace(path)
