import json
import os
import subprocess
from pathlib import Path

import pytest

from duckterm.autopilot import runs, store

GIT = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *GIT, *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec_path: str) -> Path:
    """A finished run on disk: its record, its clone with one commit on top of
    the base, and the runner's receipt pointing at spec_path."""
    monkeypatch.setattr(store, "root", lambda: tmp_path / "autopilot")
    root = runs.runs_root() / "r1"
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("one\ntwo\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("one\n2\nthree\n")
    _git(repo, "commit", "-qam", "build")
    receipt = repo / ".duckterm/runs/r-1"
    (receipt / "spec/1").mkdir(parents=True)
    (receipt / "spec/1/spec.md").write_text("# Spec\nFix it.")
    (receipt / "run.json").write_text(
        json.dumps(
            {
                "attempts": [
                    {
                        "step": "spec",
                        "attempt": 1,
                        "kind": "run",
                        "status": "completed",
                        "exit": 0,
                        "choice": None,
                        "reason": "Wrote the spec.",
                        "started_at": 100,
                        "finished_at": 147,
                        "harness": "claude-code",
                        "outputs": {"spec": {"path": spec_path}},
                        "inputs_sha256": {"issue": "x"},
                    }
                ],
                "gates": [],
                "loops": {"check": 1},
                "inputs": {"issue": json.dumps({"title": "Bug", "body": "It breaks."})},
            }
        )
    )
    runs._write_meta(
        root,
        {"id": "r1", "autopilot": "ap-1", "state": "succeeded", "started_at": 1, "base_sha": base},
    )
    return repo


def test_a_run_detail_has_each_attempt_the_issue_the_spec_and_the_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(tmp_path, monkeypatch, ".duckterm/runs/r-1/spec/1/spec.md")
    d = runs.detail("r1")
    assert d["attempts"] == [
        {
            "step": "spec",
            "attempt": 1,
            "kind": "run",
            "status": "completed",
            "exit": 0,
            "choice": None,
            "reason": "Wrote the spec.",
            "started_at": 100,
            "finished_at": 147,
            "harness": "claude-code",
        }
    ]
    assert d["loops"] == {"check": 1}
    assert d["issue_body"] == "It breaks."
    assert d["spec"] == "# Spec\nFix it."
    assert d["changes"]["files"] == [{"path": "a.py", "added": 2, "removed": 1, "binary": False}]
    assert "-two\n+2\n+three" in d["changes"]["patch"]
    assert d["changes"]["cut"] is False


@pytest.mark.parametrize("escape", ["../../outside.md", "linked.md"])
def test_a_spec_path_outside_the_clone_or_through_a_link_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, escape: str
) -> None:
    secret = tmp_path / "autopilot" / "runs" / "outside.md"
    repo = _run(tmp_path, monkeypatch, escape)
    secret.write_text("host file")
    (repo / "linked.md").symlink_to(secret)
    d = runs.detail("r1")
    assert d["spec"] is None
    assert "host file" not in json.dumps(d)


def test_a_run_from_before_base_sha_was_recorded_diffs_from_the_default_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _run(tmp_path, monkeypatch, "none.md")
    base = runs._git_out(repo, "rev-parse", "HEAD~1")
    _git(repo, "update-ref", "refs/remotes/origin/HEAD", base)
    meta = json.loads((runs.runs_root() / "r1/autopilot.json").read_text())
    del meta["base_sha"]
    runs._write_meta(runs.runs_root() / "r1", meta)
    assert [f["path"] for f in runs.detail("r1")["changes"]["files"]] == ["a.py"]


def test_an_unknown_run_is_a_key_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "root", lambda: tmp_path / "autopilot")
    with pytest.raises(KeyError):
        runs.detail("../r1")


def test_files_the_sandbox_wrote_are_read_only_up_to_their_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(tmp_path, monkeypatch, ".duckterm/runs/r-1/spec/1/spec.md")
    monkeypatch.setattr(runs, "SPEC_CAP", 6)
    monkeypatch.setattr(runs, "DIFF_CAP", 40)
    d = runs.detail("r1")
    assert d["spec"] == "# Spec"
    assert len(d["changes"]["patch"]) == 40 and d["changes"]["cut"] is True
    monkeypatch.setattr(runs, "RECEIPT_CAP", 10)
    d = runs.detail("r1")
    assert d["attempts"] == [] and d["spec"] is None


def test_the_clones_diff_driver_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the clone's .git/config is the sandbox's to write
    repo = _run(tmp_path, monkeypatch, "none.md")
    ran = tmp_path / "ran"
    driver = tmp_path / "driver.sh"
    driver.write_text(f"#!/bin/sh\ntouch {ran}\n")
    driver.chmod(0o755)
    _git(repo, "config", "diff.external", str(driver))
    assert "+three" in runs.detail("r1")["changes"]["patch"]
    assert not ran.exists()


@pytest.mark.parametrize("kind", ["fifo", "directory", "linked folder"])
def test_a_spec_that_is_not_a_regular_file_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    # a FIFO left by the sandbox blocked the reading thread for good
    repo = _run(tmp_path, monkeypatch, "out/spec.md")
    if kind == "fifo":
        (repo / "out").mkdir()
        os.mkfifo(repo / "out/spec.md")
    elif kind == "directory":
        (repo / "out/spec.md").mkdir(parents=True)
    else:
        (tmp_path / "elsewhere").mkdir()
        (tmp_path / "elsewhere/spec.md").write_text("host file")
        (repo / "out").symlink_to(tmp_path / "elsewhere")
    d = runs.detail("r1")
    assert d["spec"] is None
    assert d["attempts"][0]["step"] == "spec"
