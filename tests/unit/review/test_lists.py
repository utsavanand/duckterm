import subprocess
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
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

    result = lists.build([session("s1", clone)])
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


def test_folder_list_shows_only_its_own_sessions_prs(
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
            pull(11, "another-session/branch"),
        ],
    )
    result = lists.build([session("s2", clone)])
    assert [(r["branch"], r["pr"]["number"]) for r in result["rows"]] == [("duckterm/bbb", 9)]
    assert result["rows"][0]["session"] == {"key": "s2", "name": "s2"}


def test_no_pr_and_nothing_pushed_shows_unpushed_only_when_ahead(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lists.github, "pulls", lambda slug, token: [])
    run(clone, "checkout", "-q", "-b", "duckterm/idle")
    assert lists.build([session("s3", clone)])["rows"] == []
    commit(clone, "w.txt", "w\n", "work")
    lists._cache.clear()
    rows = lists.build([session("s3", clone)])["rows"]
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
    row = lists.build([session("s4", clone)])["rows"][0]
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
    result = lists.build([session("s5", clone)])
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
    lists.build([session("s6", clone)])
    lists.build([session("s6", clone)])
    assert len(calls) == 1
    lists.build([session("s6", clone)], refresh=True)
    assert len(calls) == 2


def test_branch_owners_reads_pushes_and_pr_creates_from_recorded_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lists.git, "repo_root", lambda directory: "/repo")
    commands = [
        ("dev", 10, "cd /tmp/wt && git push -u origin feat/x 2>&1 | tail -1", "/w"),
        ("rel", 5, "git -C /p push origin HEAD:release-v1 +refs/heads/y:refs/heads/z", "/w"),
        ("lite", 20, 'gh pr create --base main --head owner:fix/q --title "don\'t"', "/w"),
        # The command after a heredoc is read; the heredoc body is not.
        ("dev", 30, "cat > body.md <<'EOF'\nit doesn't build\nEOF\ngit push origin other", "/w"),
        # A commit message quoted across lines does not hide the push on its last line.
        ("dev", 35, 'git commit -m "Fix it\n\nTested: gate." && git push -q origin docs/x', "/w"),
        # Deletions, bare pushes and a quoted mention name no branch.
        ("dev", 40, "git push origin --delete gone; git push; echo 'git push origin nope'", "/w"),
        # The release session pushing a merge later does not take the branch.
        ("rel", 50, "git push origin feat/x", "/w"),
    ]
    assert lists.branch_owners(commands) == {
        ("/repo", "release-v1"): "rel",
        ("/repo", "z"): "rel",
        ("/repo", "feat/x"): "dev",
        ("/repo", "fix/q"): "lite",
        ("/repo", "other"): "dev",
        ("/repo", "docs/x"): "dev",
    }


@pytest.mark.parametrize(
    "command",
    [
        "git push origin :fix/owned",
        "git push origin +:fix/owned",
        "git push --dry-run origin fix/owned",
        "git push -n origin fix/owned",
        "cat <<'EOF' > notes\ngit push origin fix/owned\nEOF",
        'cat > notes <<"DOC"\ngh pr create --head fix/owned\nDOC',
    ],
)
def test_deletes_dry_runs_and_heredoc_bodies_claim_no_branch(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main-qa's regressions on #367."""
    monkeypatch.setattr(lists.git, "repo_root", lambda directory: "/repo")
    assert lists.branch_owners([("release", 1, command, "/w")]) == {}


def test_the_same_branch_name_in_two_repositories_has_two_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lists.git, "repo_root", lambda directory: directory + "-root")
    commands = [
        ("a", 1, "git push origin fix/x", "/one"),
        ("b", 2, "git push origin fix/x", "/two"),
    ]
    assert lists.branch_owners(commands) == {
        ("/one-root", "fix/x"): "a",
        ("/two-root", "fix/x"): "b",
    }


def test_pushed_branches_with_a_pr_join_the_session_beside_its_checkout(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(clone, "checkout", "-q", "-b", "duckterm/fff")
    commit(clone, "f.txt", "f\n", "f")
    monkeypatch.setattr(
        lists.github,
        "pulls",
        lambda slug, token: [
            pull(20, "duckterm/fff"),
            pull(21, "fix/from-a-temp-worktree"),
            pull(22, "release-v9", state="merged", merged_at="2026-10-22T11:00:00Z"),
            pull(23, "someone/else"),
        ],
    )
    repo = lists.git.repo_root(str(clone))
    owners = {
        (repo, "fix/from-a-temp-worktree"): "s7",
        (repo, "release-v9"): "s7",
        (repo, "pushed/no-pr"): "s7",
        (repo, "someone/else"): "other-session",
        ("/another/repo", "duckterm/fff"): "s7",
    }
    rows = lists.build([session("s7", clone)], owners=owners)["rows"]
    assert sorted(r["pr"]["number"] for r in rows) == [20, 21, 22]
    assert {r["session"]["key"] for r in rows} == {"s7"}
    merged = next(r for r in rows if r["branch"] == "release-v9")
    assert merged["status"] == "merged"


def test_push_commands_come_from_real_tool_calls_in_the_window(tmp_path: Path) -> None:
    history = HistoryStore(tmp_path / "db.sqlite")

    def bash(
        key: str, ts: int, command: str, event_type: str = "PreToolUse", **extra: object
    ) -> None:
        history.record(
            {
                "_id": f"{key}-{ts}",
                "_ts": ts,
                "event_type": event_type,
                "session_key": key,
                "cwd": "/w",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                **extra,
            }
        )

    bash("rel", 100, "git push origin release-v1")
    bash("dev", 200, "git push -u origin feat/new")
    bash("dev", 300, "pytest -q")
    bash("dev", 400, "git push origin feat/after", event_type="PostToolUse")
    bash("old", 50, "git push origin feat/too-old")
    # main-qa: a test event, or any event from a test session, cannot take a branch.
    bash("probe", 500, "git push origin feat/new", test=True)
    history.record(
        {
            "_id": "qa-start",
            "_ts": 550,
            "event_type": "SessionStart",
            "session_key": "qa",
            "test": True,
        }
    )
    bash("qa", 600, "git push origin fix/owned")
    commands = history.push_commands(100)
    assert [(key, ts, directory) for key, ts, _, directory in commands] == [
        ("rel", 100, "/w"),
        ("dev", 200, "/w"),
    ]


def test_folder_members_leave_out_test_sessions(tmp_path: Path) -> None:
    history = HistoryStore(tmp_path / "folder.sqlite")
    for key, test in (("real", False), ("synthetic", True)):
        history.record(
            {"_id": key, "_ts": 100, "event_type": "SessionStart", "session_key": key, "test": test}
        )
        history.set_meta(key, group="team")
    assert [row["session_key"] for row in lists.folder_members(history, "team")] == ["real"]
