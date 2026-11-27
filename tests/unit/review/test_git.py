import subprocess
from pathlib import Path

import pytest

from duckterm.review import git

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


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("git@github.com:utsavanand/duckterm.git", "utsavanand/duckterm"),
        ("https://github.com/utsavanand/duckterm", "utsavanand/duckterm"),
        ("https://github.com/utsavanand/duckterm.git/", "utsavanand/duckterm"),
        ("ssh://git@github.com/org/repo.name.git", "org/repo.name"),
        ("https://gitlab.com/org/repo.git", None),
    ],
)
def test_slug_from_remote_url(url: str, slug: str | None) -> None:
    assert git.slug_from_url(url) == slug


def test_conflict_detection_against_base(git_repo: Path) -> None:
    run(git_repo, "checkout", "-q", "-b", "clean")
    commit(git_repo, "other.txt", "hello\n", "add other")
    run(git_repo, "checkout", "-q", "-b", "clash", "main")
    commit(git_repo, "README.md", "clash\n", "rewrite readme on branch")
    run(git_repo, "checkout", "-q", "main")
    commit(git_repo, "README.md", "main moved\n", "rewrite readme on main")

    assert git.conflicts(git_repo, "main", "clean") is False
    assert git.conflicts(git_repo, "main", "clash") is True
    assert git.conflicts(git_repo, "main", "no-such-branch") is None


def test_ahead_behind_and_shortstat(git_repo: Path) -> None:
    run(git_repo, "checkout", "-q", "-b", "feature")
    commit(git_repo, "a.txt", "one\ntwo\n", "a")
    commit(git_repo, "b.txt", "three\n", "b")
    run(git_repo, "checkout", "-q", "main")
    commit(git_repo, "c.txt", "c\n", "c on main")

    assert git.ahead_behind(git_repo, "main", "feature") == (2, 1)
    assert git.shortstat(git_repo, "main", "feature") == {
        "files": 2,
        "additions": 3,
        "deletions": 0,
    }
    assert git.merge_base(git_repo, "main", "feature") == run(git_repo, "rev-parse", "main~1")


def test_tag_containing_picks_the_first_release(git_repo: Path) -> None:
    first = commit(git_repo, "x.txt", "x\n", "x")
    run(git_repo, "tag", "-m", "release", "v0.1.0")
    commit(git_repo, "y.txt", "y\n", "y")
    run(git_repo, "tag", "-m", "release", "v0.2.0")
    assert git.tag_containing(git_repo, first) == "v0.1.0"
    assert git.tag_containing(git_repo, run(git_repo, "rev-parse", "HEAD")) == "v0.2.0"
    assert git.tag_containing(git_repo, "0" * 40) is None


def test_default_branch_prefers_origin_head(git_repo: Path, tmp_path: Path) -> None:
    assert git.default_branch(git_repo) == "main"
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(git_repo), str(clone)], check=True)
    run(clone, "checkout", "-q", "-b", "duckterm/abc")
    assert git.default_branch(clone) == "main"
    assert git.current_branch(clone) == "duckterm/abc"
    assert git.base_ref(clone, "main") == "origin/main"
    assert git.repo_root(clone) == str(clone.resolve())


def test_remote_heads_and_fetch_track_pushes(git_repo: Path, tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(git_repo), str(clone)], check=True)
    run(clone, "config", "user.email", "t@duckterm.local")
    run(clone, "config", "user.name", "T")
    run(clone, "checkout", "-q", "-b", "duckterm/abc")
    commit(clone, "z.txt", "z\n", "z")
    assert "duckterm/abc" not in (git.remote_heads(clone) or {})
    run(clone, "push", "-q", "-u", "origin", "duckterm/abc")
    heads = git.remote_heads(clone)
    assert heads is not None and heads["duckterm/abc"] == run(clone, "rev-parse", "HEAD")
    assert git.fetch(clone, ["main", "duckterm/abc"]) is None
    assert git.ref_exists(clone, "refs/remotes/origin/duckterm/abc")


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    """A program that records each run in ran.txt, for repository config to name."""
    program = tmp_path / "planted.sh"
    program.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path / "ran.txt"}\nexit 0\n')
    program.chmod(0o755)
    return program


def ran(planted: Path) -> bool:
    return (planted.parent / "ran.txt").exists()


def test_commit_signature_check_runs_no_configured_program(git_repo: Path, planted: Path) -> None:
    raw = run(git_repo, "cat-file", "commit", "HEAD")
    header, body = raw.split("\n\n", 1)
    signature = "gpgsig -----BEGIN SSH SIGNATURE-----\n U1NIU0lH\n -----END SSH SIGNATURE-----"
    signed = subprocess.run(
        ["git", "-C", str(git_repo), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=f"{header}\n{signature}\n\n{body}\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    run(git_repo, "update-ref", "refs/heads/signed", signed)
    run(git_repo, "config", "log.showSignature", "true")
    for key in ("gpg.program", "gpg.ssh.program", "gpg.x509.program"):
        run(git_repo, "config", key, str(planted))

    assert git.commit_time(git_repo, "signed") is not None
    assert not ran(planted)


def test_conflict_check_runs_no_configured_merge_driver(git_repo: Path, planted: Path) -> None:
    commit(git_repo, ".gitattributes", "README.md merge=planted\n", "attributes")
    run(git_repo, "config", "merge.planted.driver", f"{planted} %O %A %B")
    run(git_repo, "checkout", "-q", "-b", "clash")
    commit(git_repo, "README.md", "clash\n", "rewrite readme on branch")
    run(git_repo, "checkout", "-q", "main")
    commit(git_repo, "README.md", "main moved\n", "rewrite readme on main")

    assert git.conflicts(git_repo, "main", "clash") is True
    assert not ran(planted)


def test_fetch_runs_no_configured_hook_or_upload_pack(
    git_repo: Path, tmp_path: Path, planted: Path
) -> None:
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(git_repo), str(clone)], check=True)
    hook = clone / ".git" / "hooks" / "reference-transaction"
    hook.write_text(f"#!/bin/sh\nexec {planted} hook\n")
    hook.chmod(0o755)
    run(clone, "config", "remote.origin.uploadpack", str(planted))
    commit(git_repo, "z.txt", "z\n", "z")

    assert git.fetch(clone, ["main"]) is None
    assert run(clone, "rev-parse", "origin/main") == run(git_repo, "rev-parse", "main")
    assert not ran(planted)


def test_fetch_refuses_an_ext_remote(git_repo: Path, tmp_path: Path, planted: Path) -> None:
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(git_repo), str(clone)], check=True)
    run(clone, "config", "protocol.ext.allow", "always")
    run(clone, "config", "remote.origin.url", f"ext::{planted} %S")

    assert git.fetch(clone, ["main"]) is not None
    assert not ran(planted)
