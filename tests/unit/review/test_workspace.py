import subprocess
from pathlib import Path

import pytest

from duckterm.review import lists, store, workspace

NO_SIGNING = ["-c", "tag.gpgsign=false", "-c", "commit.gpgsign=false"]


def run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *NO_SIGNING, "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def write(repo: Path, name: str, text: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)


def commit(repo: Path, message: str) -> str:
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", message)
    return run(repo, "rev-parse", "HEAD")


@pytest.fixture
def branch_repo(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(lists.git, "github_slug", lambda repo: None)
    monkeypatch.setattr(lists.git, "remote_heads", lambda repo: None)
    lists._cache.clear()
    write(git_repo, "src/duckterm/app.py", "def main():\n    return 1\n")
    write(git_repo, "src/duckterm/persistence/db.py", "TABLES = []\n")
    write(git_repo, "docs/notes.md", "# Notes\n")
    commit(git_repo, "base")
    run(git_repo, "checkout", "-q", "-b", "duckterm/feature")
    write(git_repo, "src/duckterm/app.py", "def main():\n    return 2\n")
    write(git_repo, "src/duckterm/persistence/db.py", "TABLES = ['a']\n")
    write(git_repo, "docs/notes.md", "# Notes\n\nMore.\n")
    commit(git_repo, "feature work")
    run(git_repo, "checkout", "-q", "main")
    write(git_repo, "README.md", "main moved on\n")
    commit(git_repo, "main advances")
    return git_repo


def test_resolve_and_header_describe_the_branch_against_its_merge_base(branch_repo: Path) -> None:
    target = workspace.resolve(str(branch_repo), "duckterm/feature")
    assert target.base == "main"
    assert target.head_ref == "refs/heads/duckterm/feature"
    assert target.base_sha == run(branch_repo, "rev-parse", "main~1")
    head = workspace.header(target)
    assert head["commits"] == 1
    assert head["row"]["status"] == "unpushed"
    assert head["row"]["size"] == {"files": 3, "additions": 4, "deletions": 2}
    assert head["previous_head"] is None
    with pytest.raises(workspace.ReviewError) as missing:
        workspace.resolve(str(branch_repo), "no/such/branch")
    assert missing.value.status == 404
    with pytest.raises(workspace.ReviewError) as number:
        workspace.resolve(str(branch_repo), "12")
    assert number.value.status == 404


def test_diff_buckets_files_and_viewed_state_survives_a_whitespace_push(branch_repo: Path) -> None:
    target = workspace.resolve(str(branch_repo), "duckterm/feature")
    payload = workspace.diff_payload(target, since=None, whitespace=True)
    by_path = {f["path"]: f for f in payload["files"]}
    assert [f["path"] for f in payload["files"]] == [
        "src/duckterm/persistence/db.py",
        "src/duckterm/app.py",
        "docs/notes.md",
    ]
    assert by_path["src/duckterm/persistence/db.py"]["bucket"] == "high"
    assert by_path["src/duckterm/app.py"]["bucket"] == "read"
    assert by_path["docs/notes.md"]["bucket"] == "skim"
    assert payload["counts"] == {"high": 1, "read": 1, "skim": 1, "viewed": 0, "changed_since": 0}
    assert payload["since"] is None

    app = by_path["src/duckterm/app.py"]
    workspace.mark_viewed(target, {"path": app["path"], "hash": app["hash"], "viewed": True})
    workspace.mark_viewed(
        target,
        {
            "path": "docs/notes.md",
            "hash": by_path["docs/notes.md"]["hash"],
            "viewed": True,
            "bucket": "read",
        },
    )
    again = workspace.diff_payload(target, since=None, whitespace=True)
    assert {f["path"]: f["viewed"] for f in again["files"]}["src/duckterm/app.py"] is True
    notes = next(f for f in again["files"] if f["path"] == "docs/notes.md")
    assert (notes["bucket"], notes["bucket_rule"], notes["viewed"]) == ("read", "skim", True)

    # A push that only reindents app.py keeps it viewed; a content change in
    # notes.md clears it and shows up under "since my last look".
    run(branch_repo, "checkout", "-q", "duckterm/feature")
    write(branch_repo, "src/duckterm/app.py", "def main():\n\treturn 2\n")
    write(branch_repo, "docs/notes.md", "# Notes\n\nEven more.\n")
    commit(branch_repo, "reindent and edit")
    run(branch_repo, "checkout", "-q", "main")
    lists._cache.clear()
    moved = workspace.resolve(str(branch_repo), "duckterm/feature")
    assert moved.head_sha != target.head_sha
    third = workspace.diff_payload(moved, since=None, whitespace=True)
    files = {f["path"]: f for f in third["files"]}
    assert third["since"] == target.head_sha
    assert files["src/duckterm/app.py"]["viewed"] is True
    assert files["src/duckterm/app.py"]["changed_since"] is True
    assert files["docs/notes.md"]["viewed"] is False
    assert files["docs/notes.md"]["stale"] is True
    assert files["src/duckterm/persistence/db.py"]["changed_since"] is False
    assert third["counts"]["changed_since"] == 2
    assert workspace.header(moved)["previous_head"] == target.head_sha


def test_whitespace_toggle_hides_reindents_without_changing_hashes(branch_repo: Path) -> None:
    run(branch_repo, "checkout", "-q", "duckterm/feature")
    write(branch_repo, "src/duckterm/app.py", "def main():\n    return 1\n")  # back to base content
    write(branch_repo, "docs/notes.md", "#  Notes\n\nMore.\n")
    commit(branch_repo, "undo app change, respace notes")
    run(branch_repo, "checkout", "-q", "main")
    target = workspace.resolve(str(branch_repo), "duckterm/feature")
    tight = workspace.diff_payload(target, since=None, whitespace=True)
    loose = workspace.diff_payload(target, since=None, whitespace=False)
    assert [f["path"] for f in tight["files"]] == [f["path"] for f in loose["files"]]
    notes_tight = next(f for f in tight["files"] if f["path"] == "docs/notes.md")
    notes_loose = next(f for f in loose["files"] if f["path"] == "docs/notes.md")
    assert notes_tight["hash"] == notes_loose["hash"]
    assert notes_tight["hunks"][0]["lines"] != notes_loose["hunks"][0]["lines"]
    assert notes_tight["whitespace_only"] is False
    assert "src/duckterm/app.py" not in {f["path"] for f in tight["files"]}


def test_mark_viewed_validates_its_body(branch_repo: Path) -> None:
    target = workspace.resolve(str(branch_repo), "duckterm/feature")
    for body in (
        {},
        {"path": "x"},
        {"path": "x", "hash": "h", "viewed": "yes"},
        {"path": "x", "hash": "h", "bucket": "huge"},
    ):
        with pytest.raises(workspace.ReviewError) as bad:
            workspace.mark_viewed(target, body)
        assert bad.value.status == 400
    entry = workspace.mark_viewed(
        target, {"path": "x", "hash": "h", "viewed": True, "bucket": "skim"}
    )
    assert entry["bucket"] == "skim" and entry["viewed"] is True
    cleared = workspace.mark_viewed(target, {"path": "x", "hash": "h", "bucket": ""})
    assert "bucket" not in cleared and cleared["viewed"] is True
    assert (
        store.review_dir(str(branch_repo), None, "duckterm/feature").name
        == "branch-duckterm-feature"
    )
    assert store.ref_key("307") == "pr-307"
    assert store.repo_key("/x/duckterm", "utsavanand/duckterm") == "utsavanand__duckterm"
