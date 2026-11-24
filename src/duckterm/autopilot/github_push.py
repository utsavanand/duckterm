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
    """Open a draft pull request from the run branch into the default branch,
    or return the one already open for it."""
    base = _api(token, "GET", f"/repos/{repo}")["default_branch"]
    try:
        made = _api(
            token,
            "POST",
            f"/repos/{repo}/pulls",
            {"title": title, "head": branch, "base": base, "body": body, "draft": True},
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
