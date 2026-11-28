from pathlib import Path

from duckterm.git.spotlight import spotlight_to_main
from duckterm.git.worktrees import WorktreeManager


def test_spotlight_copies_worktree_edit_to_main(git_repo: Path, tmp_path: Path) -> None:
    wt = WorktreeManager(root=tmp_path / "wt").add(git_repo, "feature")

    # Agent edits a tracked file in its worktree.
    (wt.path / "README.md").write_text("changed by the agent\n")

    changed = spotlight_to_main(repo=git_repo, worktree=wt.path)

    assert "README.md" in changed
    # The change is now visible in the MAIN checkout, not just the worktree.
    assert (git_repo / "README.md").read_text() == "changed by the agent\n"


def test_spotlight_copies_new_untracked_file(git_repo: Path, tmp_path: Path) -> None:
    wt = WorktreeManager(root=tmp_path / "wt").add(git_repo, "feature")
    (wt.path / "newfile.py").write_text("print('hi')\n")

    changed = spotlight_to_main(repo=git_repo, worktree=wt.path)

    assert "newfile.py" in changed
    assert (git_repo / "newfile.py").read_text() == "print('hi')\n"


def test_spotlight_noop_when_worktree_unchanged(git_repo: Path, tmp_path: Path) -> None:
    wt = WorktreeManager(root=tmp_path / "wt").add(git_repo, "feature")
    assert spotlight_to_main(repo=git_repo, worktree=wt.path) == []


def test_spotlight_runs_no_diff_program_from_the_repo(git_repo: Path, tmp_path: Path) -> None:
    import subprocess

    marker = tmp_path / "ran"
    program = tmp_path / "program.sh"
    program.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    program.chmod(0o755)
    wt = WorktreeManager(root=tmp_path / "wt").add(git_repo, "feat")
    (wt.path / "README.md").write_text("changed\n")
    # The worktree shares the repository's config; attributes come from it.
    subprocess.run(
        ["git", "-C", str(git_repo), "config", "diff.x.textconv", str(program)], check=True
    )
    (git_repo / ".git/info").mkdir(exist_ok=True)
    (git_repo / ".git/info/attributes").write_text("* diff=x\n")
    subprocess.run(["git", "-C", str(wt.path), "diff", "HEAD"], capture_output=True)
    assert marker.exists()  # plain git diff really runs it
    marker.unlink()
    assert spotlight_to_main(git_repo, wt.path) == ["README.md"]
    assert (git_repo / "README.md").read_text() == "changed\n"
    assert not marker.exists()
