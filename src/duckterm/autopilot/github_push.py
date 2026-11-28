"""Pushing a run's branch and opening its pull request, from the host.

The sandbox never holds a GitHub credential. The server pushes from the run's
host clone with DuckTerm's GitHub connection, and only ever:
- the run's own branch (`autopilot/<run-id>`), never a force push;
- to the autopilot's repository, named here, not the clone's remote config;
- with hooks off, so nothing in the clone runs on the host;
- never when the run changed CI configuration. A branch push runs the
  branch's own workflows with the repository's secrets, and a step's writes
  can be steered by the issue text (prompt injection), so a run must not be
  able to publish a workflow. Such a run stays on this Mac for a person.
- never when the run added or changed a symlink: a linked directory (say
  `.github/workflows` -> `ci/`) would carry CI files under paths the check
  above cannot see.
"""

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from duckterm.github_projects import repository_name

BRANCH = re.compile(r"autopilot/[0-9]{8}-[0-9]{6}-[0-9a-f]{4}")
# Files CI services execute on a push: GitHub Actions, and the common others
# a repository may also be wired to.
CI_DIRS = (".github/workflows/", ".github/actions/", ".circleci/", ".buildkite/",
           ".azure-pipelines/", ".woodpecker/", ".gitlab/ci/")  # fmt: skip
CI_FILES = frozenset({
    ".gitlab-ci.yml", ".travis.yml", "azure-pipelines.yml", "Jenkinsfile",
    "bitbucket-pipelines.yml", ".drone.yml", ".woodpecker.yml", "action.yml", "action.yaml",
})  # fmt: skip


class PushRefused(ValueError):
    pass


SYMLINK = "120000"


def ci_paths(paths: list[str]) -> list[str]:
    # The bare directory names too: one committed as a symlink lists without a slash.
    bare = {d.rstrip("/") for d in CI_DIRS} | {".github", ".gitlab"}
    return [p for p in paths if p.startswith(CI_DIRS) or p in CI_FILES or p in bare]


def changes(project: Path, base: str) -> list[tuple[str, str]]:
    """(new mode, path) for every path the run's commits touch, both sides of
    a rename. A deleted path has mode 000000."""
    proc = subprocess.run(
        ["git", "-C", str(project), "-c", "core.hooksPath=/dev/null", "diff", "--raw",
         "--no-renames", "--no-abbrev", "-z", f"{base}..HEAD"],
        capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    fields = proc.stdout.split("\0")
    # -z --raw: ":oldmode newmode oldsha newsha status" NUL path NUL, repeated.
    return [(fields[i].split()[1], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]


def push(project: Path, repo: str, branch: str, token: str, base: str) -> None:
    """Push the run branch. `base` is the commit the run started from; the
    run's own changes (base..HEAD) are checked before anything is sent."""
    repository_name(repo)
    if not BRANCH.fullmatch(branch):
        raise PushRefused(f"refusing to push {branch!r}: not an autopilot run branch")
    changed = changes(project, base)
    links = [path for mode, path in changed if mode == SYMLINK]
    if links:
        raise PushRefused(
            "the run added or changed a symlink (" + ", ".join(links[:5]) + "), which could "
            "carry CI configuration past the check; review the branch on this Mac"
        )
    blocked = ci_paths([path for _, path in changed])
    if blocked:
        raise PushRefused(
            "the run changed CI configuration (" + ", ".join(blocked[:5]) + "), which would run "
            "with the repository's secrets; review the branch on this Mac"
        )
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    # Authentication stays out of argv, remotes and the clone's config.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
        "LANG": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {auth}",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "",
    }
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "core.hooksPath=/dev/null",
            "push",
            "--no-verify",
            f"https://github.com/{repo}.git",
            f"refs/heads/{branch}:refs/heads/{branch}",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"push to {repo} failed: {proc.stderr.strip()[-300:]}")


def open_pull_request(repo: str, branch: str, title: str, body: str, token: str) -> dict[str, Any]:
    """Open a pull request from the run branch into the default branch, or
    return the one already open for it. It is not a draft: it merges only
    after the run's merge policy, and a person's approval where that asks."""
    base = _api(token, "GET", f"/repos/{repo}")["default_branch"]
    try:
        made = _api(
            token,
            "POST",
            f"/repos/{repo}/pulls",
            {"title": title, "head": branch, "base": base, "body": body},
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 422:
            raise
        owner = repo.split("/")[0]
        found = _api(token, "GET", f"/repos/{repo}/pulls?head={owner}:{branch}&state=open")
        if not found:
            raise
        made = found[0]
    return {"number": int(made["number"]), "url": str(made["html_url"]), "base": base}


# A check run that ended in any of these failed the head.
FAILED = frozenset({"failure", "timed_out", "cancelled", "action_required", "startup_failure"})
LOG_CHARS = 12000


class MergeRefused(ValueError):
    """GitHub would not merge: a conflict, branch protection or a moved head."""


def checks(repo: str, sha: str, token: str) -> tuple[str, str]:
    """CI on one commit: ("pass" | "fail" | "pending" | "none", log). The log
    names each failed check with its summary and annotations, for the step
    that fixes it. "none" means no check runs and no statuses at all."""
    runs = _api(token, "GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100")
    runs = runs.get("check_runs") or []
    status = _api(token, "GET", f"/repos/{repo}/commits/{sha}/status")
    statuses = status.get("statuses") or []
    if not runs and not statuses:
        return "none", ""
    failed = [r for r in runs if r.get("conclusion") in FAILED]
    failed_statuses = [s for s in statuses if s.get("state") in ("failure", "error")]
    if failed or failed_statuses:
        parts = []
        for r in failed:
            out = r.get("output") or {}
            notes = []
            if out.get("annotations_count"):
                for a in _api(token, "GET", f"/repos/{repo}/check-runs/{r['id']}/annotations")[:30]:
                    notes.append(
                        f"  {a.get('path')}:{a.get('start_line')}: {a.get('message', '').strip()}"
                    )
            # GitHub Actions leaves the summary empty; the job's own log says
            # what failed
            tail = (
                _job_log_tail(repo, int(r["id"]), token)
                if (r.get("app") or {}).get("slug") == "github-actions"
                else ""
            )
            parts.append(
                "\n".join(
                    [
                        f"## {r.get('name')}: {r.get('conclusion')}",
                        str(out.get("title") or ""),
                        str(out.get("summary") or ""),
                        str(out.get("text") or "")[:3000],
                        *notes,
                        tail,
                    ]
                )
            )
        for s in failed_statuses:
            parts.append(f"## {s.get('context')}: {s.get('state')}\n{s.get('description') or ''}")
        return "fail", "\n\n".join(parts)[:LOG_CHARS]
    waiting = any(r.get("status") != "completed" for r in runs)
    if waiting or (statuses and status.get("state") == "pending"):
        return "pending", ""
    return "pass", ""


JOB_LOG_TAIL = 6000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _job_log_tail(repo: str, job_id: int, token: str) -> str:
    """The lines of a GitHub Actions job's log before its first error, or its
    end when it marks none.
    GitHub answers with a redirect to a signed download URL, fetched without
    the token so it goes nowhere but GitHub."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "duckterm-autopilot"},
    )
    try:
        urllib.request.build_opener(_NoRedirect).open(req, timeout=30)
        return ""
    except urllib.error.HTTPError as exc:
        where = exc.headers.get("Location") if exc.code in (301, 302, 307) else None
    except OSError:
        return ""
    if not where or not where.startswith("https://"):
        return ""
    try:
        with urllib.request.urlopen(where, timeout=30) as resp:
            text = resp.read(2_000_000).decode(errors="replace")
    except OSError:
        return ""
    return "job log:\n" + around_first_error(text)


def around_first_error(text: str) -> str:
    """The lines before a job log's first ##[error], or its end when there is
    none, without the timestamps that cost a third of each line."""
    lines = [re.sub(r"^\S+Z ", "", line) for line in text.splitlines()]
    first = next((i for i, line in enumerate(lines) if line.startswith("##[error]")), None)
    if first is not None:
        lines = lines[max(0, first - 80) : first + 5]
    return "\n".join(lines)[-JOB_LOG_TAIL:]


def pull_head(repo: str, number: int, token: str) -> str:
    return str(_api(token, "GET", f"/repos/{repo}/pulls/{number}")["head"]["sha"])


def merge(repo: str, number: int, sha: str, title: str, token: str) -> str:
    """Squash-merge the pull request at exactly `sha`; GitHub refuses if the
    head moved. Returns the merge commit."""
    try:
        done = _api(
            token,
            "PUT",
            f"/repos/{repo}/pulls/{number}/merge",
            {"sha": sha, "merge_method": "squash", "commit_title": f"{title} (#{number})"},
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (405, 409, 422):
            try:
                why = json.loads(exc.read() or b"{}").get("message") or exc.reason
            except ValueError:
                why = exc.reason
            raise MergeRefused(str(why)) from None
        raise
    return str(done.get("sha") or "")


def _api(token: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "duckterm-autopilot",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)
