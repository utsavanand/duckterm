"""The GitHub issues trigger: which open issues should start a run.

Polled by the server (spec: every 2 minutes; a webhook needs a public
endpoint). An issue starts a run once. The first poll after a flow is put on
autopilot, or after its labels change, only records what is already there,
so turning a flow on never starts runs for old issues.

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
    if state.get("labels") != labels:
        _write(seen_path, {"labels": labels, "started": sorted(found)})
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
    if ready:
        _write(seen_path, {"labels": labels, "started": sorted(started)})
    return ready


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
