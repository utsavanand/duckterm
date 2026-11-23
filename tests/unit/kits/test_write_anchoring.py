"""Installer writes are anchored to verified directory descriptors (QA round 4):
an alias above the project is fine, a hard link is replaced not written through,
and a parent swapped for a symlink after the plan's check is refused rather than
followed into .git. The three tests came from QA round 4; the swap test now also
asserts the refusal."""

import os

import pytest
from test_skill_kits import _kit

from duckterm.kits import install, plan


def test_parent_alias_stays_in_selected_project(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    project = alias / "project"
    project.mkdir()
    install.apply(plan.build(_kit(), project, ["codex"]))
    assert (real / "project" / plan.RECORD).exists()
    assert not (real / plan.RECORD).exists()


def test_hardlink_preserves_other_name(tmp_path):
    project = tmp_path / "project"
    (project / ".codex").mkdir(parents=True)
    victim = tmp_path / "outside.toml"
    victim.write_text("# retained\n")
    target = project / ".codex/config.toml"
    os.link(victim, target)
    install.apply(plan.build(_kit(), project, ["codex"]))
    assert victim.read_text() == "# retained\n"
    assert target.stat().st_ino != victim.stat().st_ino
    install.uninstall(project, "sample-kit")
    assert victim.read_text() == "# retained\n"


def test_parent_swap_after_check_cannot_write_git(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".codex").mkdir(parents=True)
    (project / ".git").mkdir()
    victim = project / ".git/config.toml"
    victim.write_text("# git sentinel\n")
    original = install._write_atomic
    swapped = False

    def write(path, data, **kwargs):
        nonlocal swapped
        if path == project / ".codex/config.toml" and not swapped:
            swapped = True
            (project / ".codex").rename(project / ".codex-old")
            (project / ".codex").symlink_to(".git", target_is_directory=True)
        return original(path, data, **kwargs)

    monkeypatch.setattr(install, "_write_atomic", write)
    with pytest.raises(install.InstallRefused, match="not a real folder"):
        install.apply(plan.build(_kit(), project, ["codex"]))
    assert swapped
    assert victim.read_text() == "# git sentinel\n", "write followed swapped parent into .git"
