"""Release claims require packaged Git identity, not just a version string."""

import io
import json
import os
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from duckterm import __version__, build_info, update_status

STAMP = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "stamp_build.py"))


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--quiet")
    (root / "tracked.txt").write_text("synthetic build content")
    git(root, "add", "tracked.txt")
    git(
        root,
        "-c",
        "user.name=Build Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    head = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", head)
    git(root, "tag", "v" + __version__)
    return root, head


def install(monkeypatch, values):
    monkeypatch.setattr(
        build_info.importlib, "import_module", lambda name: SimpleNamespace(**values)
    )


def release_response(monkeypatch, version=None):
    monkeypatch.delenv("DUCKTERM_RELEASE_CHECK", raising=False)
    monkeypatch.setattr(
        update_status.urllib.request,
        "urlopen",
        lambda *a, **kw: io.BytesIO(
            json.dumps({"tag_name": "v" + (version or __version__)}).encode()
        ),
    )


def test_clean_tag_records_exact_commit_and_can_be_up_to_date(repository, monkeypatch):
    root, head = repository
    git(root, "checkout", "--detach", "--quiet", head)
    values = STAMP["collect"](root, __version__)
    assert values["COMMIT"] == head and values["DESCRIBE"] == "v" + __version__
    assert values["DIRTY"] is False and values["IN_MAIN"] is True
    assert values["IS_RELEASE"] is True and values["BRANCH"] is None
    install(monkeypatch, values)
    release_response(monkeypatch)
    status = update_status.status()
    assert status["installed_commit"] == head and status["installed_state"] == "released"
    assert status["update_available"] is False


@pytest.mark.parametrize("change", ["tracked", "untracked", "unmerged", "untagged"])
def test_branch_or_dirty_build_never_claims_up_to_date(repository, monkeypatch, change):
    root, _ = repository
    if change in {"tracked", "untracked"}:
        (root / ("tracked.txt" if change == "tracked" else "extra.txt")).write_text("changed")
    else:
        git(
            root,
            "-c",
            "user.name=Build Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "branch build",
        )
        if change == "untagged":
            git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    values = STAMP["collect"](root, __version__)
    assert values["IS_RELEASE"] is False
    if change == "unmerged":
        assert values["IN_MAIN"] is False
    install(monkeypatch, values)
    release_response(monkeypatch)
    status = update_status.status()
    assert status["installed_state"] == "unreleased" and status["update_available"] is None
    assert status["check_status"] == "checked" and status["latest_version"] == __version__


def test_missing_main_reference_cannot_verify_a_tag(repository, monkeypatch):
    root, _ = repository
    git(root, "update-ref", "-d", "refs/remotes/origin/main")
    values = STAMP["collect"](root, __version__)
    assert values["IN_MAIN"] is None and values["IS_RELEASE"] is None
    install(monkeypatch, values)
    release_response(monkeypatch)
    assert update_status.status()["installed_state"] == "unknown"
    assert update_status.status()["update_available"] is None


def test_no_git_or_generated_module_reports_unknown(tmp_path, monkeypatch):
    values = STAMP["collect"](tmp_path, __version__)
    assert values["COMMIT"] is None and values["IS_RELEASE"] is None

    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(build_info.importlib, "import_module", missing)
    release_response(monkeypatch)
    status = update_status.status()
    assert status["installed_state"] == "unknown" and status["update_available"] is None
    for field in ("commit", "describe", "branch", "dirty", "in_main", "is_release"):
        assert status["installed_" + field] is None


@pytest.mark.parametrize(
    "change",
    [{"VERSION": "99.0.0"}, {"COMMIT": "bad"}, {"DIRTY": True}, {"IN_MAIN": None}, {"TAG": None}],
)
def test_malformed_or_stale_stamp_never_claims_a_release(repository, monkeypatch, change):
    root, _ = repository
    values = {**STAMP["collect"](root, __version__), **change}
    install(monkeypatch, values)
    release_response(monkeypatch)
    status = update_status.status()
    assert status["installed_is_release"] is not True and status["update_available"] is None


def test_generated_module_is_complete_and_never_overwrites_existing_file(repository):
    root, head = repository
    (root / "src" / "duckterm").mkdir(parents=True)
    STAMP["stamp"](root, __version__)
    path = root / "src" / "duckterm" / "_build.py"
    original = path.read_bytes()
    values = runpy.run_path(str(path))
    assert values["COMMIT"] == head and values["VERSION"] == __version__
    with pytest.raises(FileExistsError):
        STAMP["stamp"](root, __version__)
    assert path.read_bytes() == original
