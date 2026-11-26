import contextlib
import http.server
import os
import subprocess
import threading
from collections.abc import Callable
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


def _signed_head(repo: Path) -> None:
    head, _, message = run(repo, "cat-file", "commit", "HEAD").partition("\n\n")
    # Drop a signature the host's own git config may already have added.
    head = "\n".join(line for line in head.split("\n") if not line.startswith(("gpgsig", " ")))
    signature = " -----BEGIN PGP SIGNATURE-----\n \n AAAA\n -----END PGP SIGNATURE-----"
    signed = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=f"{head}\ngpgsig{signature}\n\n{message}\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    run(repo, "reset", "-q", "--hard", signed)


# Each case arranges a repository whose config names `program`, and returns
# the plain git command that runs it plus the DuckTerm call that must not.
Case = tuple[list[str], Callable[[], object]]


def _verifier(repo: Path, program: str) -> Case:
    _signed_head(repo)
    run(repo, "config", "log.showSignature", "true")
    run(repo, "config", "gpg.program", program)
    return ["log", "-1"], lambda: git.run(repo, "log", "-1")


def _lazy_fetch(repo: Path, program: str) -> Case:
    run(repo, "config", "core.repositoryformatversion", "1")
    run(repo, "config", "extensions.partialClone", "origin")
    run(repo, "config", "remote.origin.url", "ssh://example.invalid/x")
    run(repo, "config", "remote.origin.promisor", "true")
    run(repo, "config", "core.sshCommand", program)
    (repo / ".git/refs/tags/v9").write_text("12" * 20 + "\n")
    args = ["tag", "--list", "v*", "--sort=-creatordate"]
    return args, lambda: git.run(repo, *args)


def _ssh_command(repo: Path, program: str) -> Case:
    run(repo, "remote", "add", "origin", "ssh://git@127.0.0.1:1/x")
    run(repo, "config", "core.sshCommand", program)
    return ["ls-remote", "--heads", "origin"], lambda: git.remote_heads(repo)


def _fetch_hook(repo: Path, program: str) -> Case:
    clone = repo.parent / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    commit(repo, "n.txt", "n\n", "new")
    hook = clone / ".git/hooks/reference-transaction"
    hook.write_text(f"#!/bin/sh\nexec {program}\n")
    hook.chmod(0o755)
    return ["-C", str(clone), "fetch", "-q", "origin"], lambda: git.fetch(clone, ["main"])


def _upload_pack(repo: Path, program: str) -> Case:
    clone = repo.parent / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    run(clone, "config", "remote.origin.uploadpack", program)
    return ["-C", str(clone), "ls-remote", "origin"], lambda: git.remote_heads(clone)


@pytest.mark.parametrize(
    "arrange", [_verifier, _lazy_fetch, _ssh_command, _fetch_hook, _upload_pack]
)
def test_a_repos_config_cannot_run_a_program(git_repo: Path, tmp_path: Path, arrange) -> None:
    # Sessions report the folders review reads and can write their .git/config.
    marker = tmp_path / "ran"
    program = tmp_path / "program.sh"
    program.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    program.chmod(0o755)
    args, call = arrange(git_repo, str(program))
    subprocess.run(["git", "-C", str(git_repo), *args], capture_output=True)
    assert marker.exists()  # plain git really runs it
    marker.unlink()
    with contextlib.suppress(git.ReviewGitError):
        call()
    assert not marker.exists()


class _AskForPassword(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


def test_the_owners_credential_helper_runs_and_the_repos_does_not(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = http.server.HTTPServer(("127.0.0.1", 0), _AskForPassword)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        markers = {}
        for who in ("owner", "repo"):
            markers[who] = tmp_path / f"{who}-ran"
            helper = tmp_path / f"{who}.sh"
            helper.write_text(f"#!/bin/sh\ntouch {markers[who]}\n")
            helper.chmod(0o755)
        (tmp_path / "home").mkdir()
        (tmp_path / "home/.gitconfig").write_text(
            f"[credential]\n\thelper = !{tmp_path / 'owner.sh'}\n"
        )
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        url = f"http://127.0.0.1:{server.server_port}/x"
        run(git_repo, "remote", "add", "origin", url)
        run(git_repo, "config", "credential.helper", f"!{tmp_path / 'repo.sh'}")
        monkeypatch.setattr(git, "_REMOTE_PROTOCOLS", ("http",))
        subprocess.run(
            ["git", "-C", str(git_repo), "ls-remote", "origin"],
            capture_output=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        assert markers["repo"].exists()  # plain git really runs it
        markers["repo"].unlink()
        markers["owner"].unlink()
        assert git.remote_heads(git_repo) is None
        assert markers["owner"].exists() and not markers["repo"].exists()
    finally:
        server.shutdown()


@pytest.mark.parametrize(
    "read",
    [
        lambda repo, ref: git.commit_time(repo, ref),
        lambda repo, ref: git.ahead_behind(repo, ref, "main"),
        lambda repo, ref: git.shortstat(repo, ref, "main"),
    ],
    ids=["commit_time", "ahead_behind", "shortstat"],
)
def test_a_branch_named_like_an_option_is_never_an_option(
    git_repo: Path, tmp_path: Path, read
) -> None:
    # A range like "<ref>...main" makes git write to "<path>...main".
    with contextlib.suppress(git.ReviewGitError):
        read(git_repo, f"--output={tmp_path / 'written'}")
    assert not list(tmp_path.glob("written*"))
