"""The Fleet monitor's Pulse reads PR merges and release tags from the clones
sessions work in, plus artifacts made, and nothing else."""

import asyncio
import base64
import subprocess
import threading
import time

import pytest
from tests.runtime.test_relay import world  # noqa: F401  (fixture)
from tests.runtime.test_session_api import Writer, dispatch

from duckterm.core import fleet_pulse
from duckterm.editions import EditionConfig

GIT = [
    "git",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
    "-c",
    "user.name=t",
    "-c",
    "user.email=t@t",
]


def git(repo, *args):
    subprocess.run([*GIT, "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "duckterm"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "commit", "-q", "--allow-empty", "-m", "start")
    git(path, "checkout", "-q", "-b", "oracle-reliability")
    git(path, "commit", "-q", "--allow-empty", "-m", "fix")
    git(path, "checkout", "-q", "main")
    git(
        path,
        "merge",
        "-q",
        "--no-ff",
        "oracle-reliability",
        "-m",
        "Merge pull request #470 from me/oracle-reliability",
        "-m",
        "Needs you counts match",
    )
    git(path, "commit", "-q", "--allow-empty", "-m", "not a PR merge")
    git(path, "tag", "-a", "v0.4.218", "-m", "Release v0.4.218")
    return path


def session(repo, **kw):
    return {
        "session_key": "s1",
        "name": "oracle-dev",
        "repo_path": str(repo),
        "branch": "oracle-reliability",
        "updated_at": int(time.time() * 1000),
        **kw,
    }


def test_merged_pr_names_the_session_whose_branch_it_was(repo) -> None:
    events = fleet_pulse.collect([session(repo)], [], int(time.time() * 1000))
    pr = next(e for e in events if e["kind"] == "pr")
    assert pr["title"] == "#470 Needs you counts match"
    assert (pr["session_key"], pr["name"], pr["repo"]) == ("s1", "oracle-dev", "duckterm")
    release = next(e for e in events if e["kind"] == "release")
    assert release["title"] == "v0.4.218" and release["detail"] == "1 merged PR"
    assert {e["kind"] for e in events} == {"pr", "release"}


def test_two_worktrees_of_one_repo_are_read_once(repo, tmp_path) -> None:
    other = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(other), "-b", "other")
    rows = [session(repo), session(other, session_key="s2", branch="other")]
    assert len(fleet_pulse.clones(rows, 0)) == 1
    events = fleet_pulse.collect(rows, [], int(time.time() * 1000))
    assert [e["kind"] for e in events].count("pr") == 1


def test_old_sessions_and_old_merges_stay_out(repo) -> None:
    now = int(time.time() * 1000)
    stale = session(repo, updated_at=now - fleet_pulse.WINDOW_MS - 1)
    assert fleet_pulse.collect([stale], [], now) == []
    future = now + 2 * fleet_pulse.WINDOW_MS
    assert fleet_pulse.collect([session(repo, updated_at=future)], [], future) == []


def test_one_sessions_burst_of_artifacts_is_one_event() -> None:
    hour = fleet_pulse.ARTIFACT_BURST_MS
    now = int(time.time() * 1000)
    made = [
        {"session_key": key, "session_name": key, "title": f"{key} {i}", "created_at": now - at}
        for key, i, at in [
            ("a", 1, 0),
            ("a", 2, hour // 2),
            ("a", 3, hour),
            ("b", 1, 1000),
            ("a", 4, 3 * hour),
        ]
    ]
    events = fleet_pulse.collect([], made, now)
    assert [(e["name"], e["count"], e["title"]) for e in events] == [
        ("a", 3, "a 1"),
        ("b", 1, "b 1"),
        ("a", 1, "a 4"),
    ]


def test_a_repos_signature_verifier_never_runs(repo, tmp_path) -> None:
    # Security #485: a session names the folder, so its .git/config must not
    # get a program run as the server via log.showSignature.
    marker = tmp_path / "ran"
    verifier = tmp_path / "verify.sh"
    verifier.write_text(f"#!/bin/sh\ntouch {marker}\n")
    verifier.chmod(0o755)
    commit = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "commit", "HEAD~1"], capture_output=True, text=True
    ).stdout
    head, _, message = commit.partition("\n\n")
    signature = " -----BEGIN PGP SIGNATURE-----\n \n AAAA\n -----END PGP SIGNATURE-----"
    signed = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=f"{head}\ngpgsig{signature}\n\n{message}",
        capture_output=True,
        text=True,
    ).stdout.strip()
    git(repo, "reset", "-q", "--hard", signed)
    git(repo, "config", "log.showSignature", "true")
    git(repo, "config", "gpg.program", str(verifier))
    subprocess.run(["git", "-C", str(repo), "log", "-1", "--merges"], capture_output=True)
    assert marker.exists()  # the signed PR merge really runs it
    marker.unlink()
    fleet_pulse.collect([session(repo)], [], int(time.time() * 1000))
    assert not marker.exists()


def test_a_branch_named_like_an_option_is_never_an_option(repo, tmp_path) -> None:
    # Security #485: origin/HEAD pointing at "--output=<path>" made git log
    # write the merge log to that path.
    target = tmp_path / "written"
    git(repo, "update-ref", f"refs/heads/--output={target}", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/heads/--output={target}")
    events = fleet_pulse.collect([session(repo)], [], int(time.time() * 1000))
    assert not target.exists()
    assert [e["title"] for e in events if e["kind"] == "pr"] == ["#470 Needs you counts match"]


def test_work_per_refresh_is_bounded(repo, tmp_path, monkeypatch) -> None:
    other = tmp_path / "other"
    subprocess.run(["cp", "-R", str(repo), str(other)], check=True)
    rows = [session(repo), session(other, session_key="s2")]
    assert len(fleet_pulse.clones(rows, 0)) == 2
    monkeypatch.setattr(fleet_pulse, "MAX_PATHS", 1)
    assert len(fleet_pulse.clones(rows, 0)) == 1
    monkeypatch.setattr(fleet_pulse, "DEADLINE_S", -1.0)
    asked = []
    monkeypatch.setattr(fleet_pulse.git, "run", lambda *a, **k: asked.append(a) or "")
    assert fleet_pulse.collect(rows, [], int(time.time() * 1000)) == []
    assert asked == []  # not even a rev-parse once the time is up


def test_overlapping_polls_share_one_refresh(world, monkeypatch) -> None:  # noqa: F811
    server = world[0]
    calls = []
    release = threading.Event()

    def collect(sessions, artifacts, now):
        calls.append(now)
        release.wait(5)
        return [{"kind": "release", "at": now, "title": "v1", "repo": "r", "detail": ""}]

    monkeypatch.setattr(fleet_pulse, "collect", collect)

    async def polls() -> list[Writer]:
        writers = [Writer(), Writer()]
        first = asyncio.ensure_future(server._fleet_pulse(writers[0]))
        await asyncio.sleep(0.05)
        second = asyncio.ensure_future(server._fleet_pulse(writers[1]))
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.gather(first, second)
        return writers

    writers = asyncio.run(polls())
    assert len(calls) == 1
    assert all(b'"v1"' in w.data for w in writers)


def test_route_needs_the_flag_and_returns_artifacts(world) -> None:  # noqa: F811
    server, owner, _, history = world
    assert dispatch(server, "GET", "/fleet/pulse", owner)[0] == 403
    server.edition = EditionConfig(server.edition.name, server.edition.features | {"fleet_monitor"})
    history.artifacts.register(
        "pm",
        {
            "title": "Spec draft: Fleet monitor",
            "source_path": "/tmp/spec.md",
            "content_base64": base64.b64encode(b"# Spec").decode(),
            "kind": "spec",
        },
    )
    status, body = dispatch(server, "GET", "/fleet/pulse", owner)
    assert status == 200
    assert [(e["kind"], e["title"], e["name"]) for e in body["events"]] == [
        ("artifact", "Spec draft: Fleet monitor", "product-manager")
    ]


def test_home_reads_the_same_pulse_under_its_own_flag(world) -> None:  # noqa: F811
    # The owner runs Home without the Fleet monitor: /home/pulse follows `home`,
    # /fleet/pulse still follows `fleet_monitor`.
    server, owner, _, _ = world
    assert dispatch(server, "GET", "/home/pulse", owner)[0] == 403
    server.edition = EditionConfig(server.edition.name, server.edition.features | {"home"})
    status, body = dispatch(server, "GET", "/home/pulse", owner)
    assert status == 200 and body == {"events": []}
    assert dispatch(server, "GET", "/fleet/pulse", owner)[0] == 403
