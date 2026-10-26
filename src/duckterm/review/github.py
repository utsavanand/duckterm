"""GitHub REST calls for pull request status, through the owner's connector
token. urllib only; one bounded request per call."""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from duckterm.github_projects import NoRedirect

API = "https://api.github.com"
MAX_BODY = 8 * 1024 * 1024


class GitHubError(RuntimeError):
    pass


def token() -> tuple[str | None, str | None]:
    """The owner's GitHub token, or the reason there is none."""
    from duckterm import connectors

    state = connectors.policy("github")
    if not state.get("enabled"):
        return None, "Enable the GitHub connector to see pull request status"
    credential = connectors.github_token(str(state.get("source")))
    if not credential:
        return None, "Reconnect the GitHub connector to see pull request status"
    return credential[0], None


def request(
    path: str, token: str, *, method: str = "GET", body: dict[str, Any] | None = None
) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "duckterm",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
            raw = response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(ValueError, OSError, AttributeError):
            detail = str(json.loads(exc.read(4096)).get("message") or "")
        if exc.code == 403 and "rate limit" in detail.lower():
            raise GitHubError("GitHub rate limit reached; status will refresh later") from exc
        raise GitHubError(detail or f"GitHub returned {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise GitHubError("GitHub is unreachable") from exc
    if len(raw) > MAX_BODY:
        raise GitHubError("GitHub response was too large")
    return json.loads(raw) if raw else None


def pulls(slug: str, token: str) -> list[dict[str, Any]]:
    """Every pull request the list endpoint returns, newest activity first.
    One request; 100 rows covers a repository's live set."""
    rows = request(f"/repos/{slug}/pulls?state=all&per_page=100&sort=updated&direction=desc", token)
    if not isinstance(rows, list):
        raise GitHubError("Unexpected pull request response")
    return [summarise_pull(row) for row in rows]


def summarise_pull(row: dict[str, Any]) -> dict[str, Any]:
    merged_at = row.get("merged_at")
    state = "merged" if merged_at else str(row.get("state") or "open")
    return {
        "number": int(row["number"]),
        "title": str(row.get("title") or ""),
        "body": str(row.get("body") or ""),
        "url": str(row.get("html_url") or ""),
        "state": state,
        "draft": bool(row.get("draft")),
        "author": str((row.get("user") or {}).get("login") or ""),
        "head": str((row.get("head") or {}).get("ref") or ""),
        "head_sha": str((row.get("head") or {}).get("sha") or ""),
        "base": str((row.get("base") or {}).get("ref") or ""),
        "merge_commit_sha": row.get("merge_commit_sha"),
        "merged_at": merged_at,
        "updated_at": row.get("updated_at"),
        "created_at": row.get("created_at"),
    }


def check_runs(slug: str, sha: str, token: str) -> dict[str, int]:
    data = request(f"/repos/{slug}/commits/{sha}/check-runs?per_page=100", token)
    runs = data.get("check_runs", []) if isinstance(data, dict) else []
    summary = {"total": len(runs), "failed": 0, "pending": 0, "passed": 0}
    for run in runs:
        if run.get("status") != "completed":
            summary["pending"] += 1
        elif run.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required"):
            summary["failed"] += 1
        else:
            summary["passed"] += 1
    return summary


def review_state(slug: str, number: int, token: str) -> str:
    """'approved', 'changes_requested' or 'none': the latest substantive
    review per reviewer decides, as GitHub's merge box does."""
    rows = request(f"/repos/{slug}/pulls/{number}/reviews?per_page=100", token)
    latest: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        state = str(row.get("state") or "")
        if state not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            continue
        login = str((row.get("user") or {}).get("login") or "")
        latest[login] = state
    states = set(latest.values())
    if "CHANGES_REQUESTED" in states:
        return "changes_requested"
    if "APPROVED" in states:
        return "approved"
    return "none"


def post_review(slug: str, number: int, event: str, body: str, token: str) -> dict[str, Any]:
    if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
        raise GitHubError("Unknown review event")
    result = request(
        f"/repos/{slug}/pulls/{number}/reviews",
        token,
        method="POST",
        body={"event": event, "body": body},
    )
    return {"id": result.get("id"), "url": result.get("html_url"), "state": result.get("state")}
