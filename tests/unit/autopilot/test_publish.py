"""A successful run's branch is pushed from the host and gets a draft pull
request; nothing else is pushed, and the token never reaches argv."""

import subprocess
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from duckterm.autopilot import github_push, runs

BRANCH = "autopilot/20261122-155703-48eb"


def test_only_a_run_branch_can_be_pushed(tmp_path: Path) -> None:
    for branch in ("main", "autopilot/../main", "autopilot/x", "refs/heads/main"):
        with pytest.raises(ValueError, match="not an autopilot run branch"):
            github_push.push(tmp_path, "o/r", branch, "tok")


def test_push_names_the_repository_and_one_ref_and_keeps_the_token_out_of_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(argv=argv, env=kw["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(github_push.subprocess, "run", run)
    github_push.push(tmp_path, "o/r", BRANCH, "secret-token")
    argv = seen["argv"]
    assert argv[-2:] == ["https://github.com/o/r.git", f"refs/heads/{BRANCH}:refs/heads/{BRANCH}"]
    assert "core.hooksPath=/dev/null" in argv and "--no-verify" in argv
    assert not any(a.startswith(("-f", "--force", "+")) for a in argv)
    assert all("secret-token" not in a for a in argv)
    assert "secret-token" not in seen["env"]["GIT_CONFIG_VALUE_0"]  # base64, in env only


def test_an_existing_pull_request_for_the_branch_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def api(token: str, method: str, path: str, body: Any = None) -> Any:
        calls.append((method, path))
        if path == "/repos/o/r":
            return {"default_branch": "main"}
        if method == "POST":
            raise urllib.error.HTTPError(path, 422, "exists", {}, None)  # type: ignore[arg-type]
        return [{"number": 7, "html_url": "https://github.com/o/r/pull/7"}]

    monkeypatch.setattr(github_push, "_api", api)
    pr = github_push.open_pull_request("o/r", BRANCH, "t", "b", "tok")
    assert pr == {"number": 7, "url": "https://github.com/o/r/pull/7", "base": "main"}
    assert calls[-1] == ("GET", f"/repos/o/r/pulls?head=o:{BRANCH}&state=open")


def test_the_body_carries_the_spec_and_closes_the_issue_but_skips_a_linked_spec(
    tmp_path: Path,
) -> None:
    (tmp_path / "spec.md").write_text("# Spec\nFix it.")
    (tmp_path / "secret").write_text("host file")
    (tmp_path / "linked.md").symlink_to(tmp_path / "secret")
    meta = {"id": "r1", "flow": "autopilot/issue-to-merge"}
    item = {"number": 315, "title": "Row cannot be collapsed"}
    state = {"attempts": [{"step": "spec", "outputs": {"spec": {"path": "spec.md"}}}]}
    body = runs.pr_body(meta, item, tmp_path, state)
    assert body.startswith("# Spec\nFix it.") and "Closes #315." in body and "draft" in body
    linked = {"attempts": [{"step": "spec", "outputs": {"spec": {"path": "linked.md"}}}]}
    assert "host file" not in runs.pr_body(meta, item, tmp_path, linked)
    assert runs.pr_title(item) == "Fix #315: Row cannot be collapsed"


def test_a_run_that_changed_nothing_opens_no_pull_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a").write_text("a")
    git = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "add", "a"], check=True)
    subprocess.run([*git, "commit", "-qm", "a"], check=True)
    head = runs._git_out(repo, "rev-parse", "HEAD")
    root = tmp_path / "run"
    runs._write_meta(root, {"id": "r1"})
    monkeypatch.setattr(github_push, "push", lambda *a: pytest.fail("pushed"))
    runs._publish(root, {"repo": "o/r"}, {"id": "r1", "branch": BRANCH}, {}, repo, head, {})
    assert "changed no files" in (root / "autopilot.json").read_text()
