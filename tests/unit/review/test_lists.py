import subprocess
from pathlib import Path

import pytest

from duckterm.review import lists

NO_SIGNING = ["-c", "tag.gpgsign=false", "-c", "tag.forceSignAnnotated=false"]


def run(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "tag.forceSignAnnotated=false",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit(repo: Path, name: str, text: str, message: str) -> str:
    (repo / name).write_text(text)
    run(repo, "add", name)
    run(repo, "commit", "-q", "-m", message)
    return run(repo, "rev-parse", "HEAD")


def pull(number: int, head: str, **extra: object) -> dict[str, object]:
    row = {
        "number": number,
        "title": f"PR {number}",
        "body": "",
        "url": f"https://github.com/o/r/pull/{number}",
        "state": "open",
        "draft": False,
        "author": "bot",
        "head": head,
        "head_sha": "0" * 40,
        "base": "main",
        "merge_commit_sha": None,
        "merged_at": None,
        "updated_at": "2026-10-22T10:00:00Z",
        "created_at": "2026-10-22T09:00:00Z",
    }
    row.update(extra)
    return row


@pytest.fixture
def clone(git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clone with `origin`, so pushes and remote heads are real, and GitHub
    stubbed to a fixed pull list."""
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(git_repo), str(clone)], check=True)
    run(clone, "config", "user.email", "t@duckterm.local")
    run(clone, "config", "user.name", "T")
    run(clone, "remote", "set-url", "--push", "origin", str(git_repo))
    monkeypatch.setattr(lists.git, "github_slug", lambda repo: "o/r")
    monkeypatch.setattr(lists.github, "token", lambda: ("tok", None))
    monkeypatch.setattr(
        lists.github, "check_runs", lambda *a: {"total": 2, "failed": 0, "pending": 0, "passed": 2}
    )
    monkeypatch.setattr(lists.github, "review_state", lambda *a: "none")
    lists._cache.clear()
    return clone


def session(key: str, path: Path, grp: str = "team") -> dict[str, object]:
    return {"session_key": key, "name": key, "worktree_path": str(path), "cwd": None, "grp": grp}


def test_claims_pr_by_branch_and_reports_unpushed_work(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(clone, "checkout", "-q", "-b", "duckterm/aaa")
    commit(clone, "a.txt", "a\n", "a")
    run(clone, "push", "-q", "-u", "origin", "duckterm/aaa")
    commit(clone, "a2.txt", "a2\n", "a2 not pushed")
    monkeypatch.setattr(
        lists.github,
        "pulls",
        lambda slug, token: [pull(5, "duckterm/aaa"), pull(6, "someone/else")],
    )

    result = lists.build([session("s1", clone)], include_unclaimed=False)
    assert [r["branch"] for r in result["rows"]] == ["duckterm/aaa"]
    row = result["rows"][0]
    assert row["pr"]["number"] == 5
    assert row["session"] == {"key": "s1", "name": "s1"}
    assert row["status"] == "open"
    assert row["pushed"] is True
    assert row["unpushed_commits"] == 1
    assert row["ahead"] == 2
    assert row["size"] == {"files": 2, "additions": 2, "deletions": 0}
    assert row["conflict"] is False
    assert result["repos"][0]["connected"] is True


def test_folder_list_adds_unclaimed_open_prs_and_drops_stale_closed_ones(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(clone, "checkout", "-q", "-b", "duckterm/bbb")
    commit(clone, "b.txt", "b\n", "b")
    monkeypatch.setattr(
        lists.github,
        "pulls",
        lambda slug, token: [
            pull(9, "duckterm/bbb"),
            pull(10, "hand/made", author="utsav"),
            pull(3, "old/closed", state="closed", updated_at="2025-01-01T00:00:00Z"),
        ],
    )
    result = lists.build([session("s2", clone)], include_unclaimed=True)
    by_branch = {r["branch"]: r for r in result["rows"]}
    assert set(by_branch) == {"duckterm/bbb", "hand/made"}
    assert by_branch["hand/made"]["session"] is None
    assert by_branch["hand/made"]["author"] == "utsav"
    assert by_branch["duckterm/bbb"]["pr"]["number"] == 9


def test_no_pr_and_nothing_pushed_shows_unpushed_only_when_ahead(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lists.github, "pulls", lambda slug, token: [])
    run(clone, "checkout", "-q", "-b", "duckterm/idle")
    assert lists.build([session("s3", clone)], include_unclaimed=False)["rows"] == []
    commit(clone, "w.txt", "w\n", "work")
    lists._cache.clear()
    rows = lists.build([session("s3", clone)], include_unclaimed=False)["rows"]
    assert [(r["status"], r["label"]) for r in rows] == [("unpushed", "Not pushed")]
    assert rows[0]["compare_url"] is None


def test_merged_pr_names_the_release_tag(
    git_repo: Path, clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(clone, "checkout", "-q", "-b", "duckterm/ccc")
    sha = commit(clone, "c.txt", "c\n", "c")
    run(clone, "push", "-q", "-u", "origin", "duckterm/ccc")
    # The merge and the release tag happen upstream, as they do on GitHub.
    run(git_repo, "merge", "-q", "--no-ff", "-m", "merge ccc", "duckterm/ccc")
    merge_sha = run(git_repo, "rev-parse", "HEAD")
    run(git_repo, "tag", "-m", "release", "v1.2.3")
    monkeypatch.setattr(
        lists.github,
        "pulls",
        lambda slug, token: [
            pull(
                12,
                "duckterm/ccc",
                state="merged",
                merged_at="2026-10-22T11:00:00Z",
                merge_commit_sha=merge_sha,
                head_sha=sha,
            )
        ],
    )
    row = lists.build([session("s4", clone)], include_unclaimed=False)["rows"][0]
    assert row["status"] == "merged"
    assert row["label"] == "Merged · v1.2.3"
    assert row["version"] == "v1.2.3"
    assert row["checks"] is None


def test_github_errors_keep_git_facts(clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(slug: str, token: str) -> list[dict[str, object]]:
        raise lists.github.GitHubError("GitHub rate limit reached; status will refresh later")

    monkeypatch.setattr(lists.github, "pulls", boom)
    run(clone, "checkout", "-q", "-b", "duckterm/ddd")
    commit(clone, "d.txt", "d\n", "d")
    run(clone, "push", "-q", "-u", "origin", "duckterm/ddd")
    result = lists.build([session("s5", clone)], include_unclaimed=True)
    assert result["rows"][0]["status"] == "pushed"
    assert (
        result["rows"][0]["compare_url"]
        == "https://github.com/o/r/compare/main...duckterm/ddd?expand=1"
    )
    assert result["repos"][0]["error"] == "GitHub rate limit reached; status will refresh later"


def test_cache_serves_within_ttl_and_refresh_bypasses_it(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def pulls(slug: str, token: str) -> list[dict[str, object]]:
        calls.append(1)
        return []

    monkeypatch.setattr(lists.github, "pulls", pulls)
    run(clone, "checkout", "-q", "-b", "duckterm/eee")
    commit(clone, "e.txt", "e\n", "e")
    lists.build([session("s6", clone)], include_unclaimed=False)
    lists.build([session("s6", clone)], include_unclaimed=False)
    assert len(calls) == 1
    lists.build([session("s6", clone)], include_unclaimed=False, refresh=True)
    assert len(calls) == 2
