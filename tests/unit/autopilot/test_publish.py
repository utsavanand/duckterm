"""A successful run's branch is pushed from the host and gets a draft pull
request; nothing else is pushed, and the token never reaches argv."""

import json
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
            github_push.push(tmp_path, "o/r", branch, "tok", "base")


def test_push_names_the_repository_and_one_ref_and_keeps_the_token_out_of_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(argv=argv, env=kw["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(github_push.subprocess, "run", run)
    monkeypatch.setattr(github_push, "changes", lambda project, base: [("100644", "src/a.py")])
    github_push.push(tmp_path, "o/r", BRANCH, "secret-token", "base")
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
    assert runs.pr_title(meta, item) == "Fix #315: Row cannot be collapsed"


def test_a_pull_request_for_an_issue_in_another_repository_names_that_repository(
    tmp_path: Path,
) -> None:
    # Kaho's reports are filed in their own repository; the fix lands in the
    # app's. A bare #7 would point at the app repository's own #7.
    meta = {"id": "r1", "flow": "f", "repo": "o/kaho", "issue_repo": "o/kaho-reports"}
    item = {"number": 7, "title": "Card is blank"}
    assert runs.pr_title(meta, item) == "Fix o/kaho-reports#7: Card is blank"
    assert "Closes o/kaho-reports#7." in runs.pr_body(meta, item, tmp_path, {})
    same = {**meta, "issue_repo": "o/kaho"}
    assert runs.pr_title(same, item) == "Fix #7: Card is blank"


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


def _repo_with_change(
    tmp_path: Path, path: str, rename_from: str | None = None
) -> tuple[Path, str]:
    repo = tmp_path / "repo"
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
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("x\n")
    if rename_from:
        (repo / rename_from).parent.mkdir(parents=True, exist_ok=True)
        (repo / rename_from).write_text("on: push\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    base = runs._git_out(repo, "rev-parse", "HEAD")
    if rename_from:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([*git, "mv", rename_from, path], check=True)
    else:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("on: push\njobs: {}\n")
        subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "step"], check=True)
    return repo, base


@pytest.mark.parametrize(
    ("path", "rename_from"),
    [
        (".github/workflows/exfil.yml", None),
        (".github/actions/setup/action.yml", None),
        (".gitlab-ci.yml", None),
        (".github/workflows/ci.yml", "docs/ci.yml"),  # renamed into place
        ("docs/old.yml", ".github/workflows/old.yml"),  # renamed away counts too
    ],
)
def test_a_run_that_changed_ci_configuration_is_never_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, rename_from: str | None
) -> None:
    repo, base = _repo_with_change(tmp_path, path, rename_from)
    sent: list[list[str]] = []
    real = subprocess.run

    def run(argv: list[str], **kw: Any) -> Any:
        if "push" in argv:
            sent.append(argv)
        return real(argv, **kw)

    monkeypatch.setattr(github_push.subprocess, "run", run)
    with pytest.raises(github_push.PushRefused, match="CI configuration"):
        github_push.push(repo, "o/r", BRANCH, "tok", base)
    assert sent == []
    # Through a whole publish: recorded on the run, no pull request.
    monkeypatch.setattr(runs.github_projects, "local_token", lambda: ("tok", {}))
    monkeypatch.setattr(github_push, "open_pull_request", lambda *a: pytest.fail("opened a PR"))
    root = tmp_path / "run"
    runs._write_meta(root, {"id": "r1"})
    runs._publish(root, {"repo": "o/r"}, {"id": "r1", "branch": BRANCH}, {}, repo, base, {})
    meta = json.loads((root / "autopilot.json").read_text())
    assert meta["pushed"] is False and "CI configuration" in meta["pr_skipped"]
    assert sent == []


def test_an_ordinary_push_failure_is_recorded_and_the_run_stays_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo_with_change(tmp_path, "src/fix.py")
    monkeypatch.setattr(runs.github_projects, "local_token", lambda: ("tok", {}))

    def fail(*a: Any) -> None:
        raise RuntimeError("push to o/r failed: network")

    monkeypatch.setattr(github_push, "push", fail)
    root = tmp_path / "run"
    runs._write_meta(root, {"id": "r1", "state": "succeeded"})
    runs._publish(root, {"repo": "o/r"}, {"id": "r1", "branch": BRANCH}, {}, repo, base, {})
    meta = json.loads((root / "autopilot.json").read_text())
    assert meta["state"] == "succeeded" and "network" in meta["pr_error"]


@pytest.mark.parametrize("linked", [".github/workflows", ".circleci", ".gitlab/ci", "src/tools"])
def test_a_run_that_committed_a_symlink_is_never_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked: str
) -> None:
    # .github/workflows -> ../ci with ci/x.yml inside lists as ".github/workflows"
    # and "ci/x.yml", neither under a CI directory; any added symlink is refused.
    repo, base = _repo_with_change(tmp_path, "ci/x.yml")
    (repo / linked).parent.mkdir(parents=True, exist_ok=True)
    (repo / linked).symlink_to(Path("../" * linked.count("/")) / "ci")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
           "-c", "commit.gpgsign=false"]  # fmt: skip
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "link"], check=True)
    assert (github_push.SYMLINK, linked) in github_push.changes(repo, base)
    real = subprocess.run
    monkeypatch.setattr(
        github_push.subprocess,
        "run",
        lambda argv, **kw: pytest.fail("pushed") if "push" in argv else real(argv, **kw),
    )
    with pytest.raises(github_push.PushRefused, match="symlink"):
        github_push.push(repo, "o/r", BRANCH, "tok", base)


def test_a_ci_directory_name_without_its_slash_is_still_ci() -> None:
    names = [".github/workflows", ".github", ".circleci", ".buildkite", ".gitlab/ci", ".gitlab"]
    assert github_push.ci_paths([*names, "src/.github.md", ".githubx"]) == names
