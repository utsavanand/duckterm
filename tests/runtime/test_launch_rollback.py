"""A failed launch must not leave a git worktree, branch, or dead supervisor
behind. Before this, a typo'd command (which the PTY path turns into a
ValueError) aborted after the worktree was already created — junk accreted on
every failed launch."""

import asyncio
from pathlib import Path

import pytest

import duckterm.core.orchestrator as orch_mod
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.git.worktrees import WorktreeManager
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime


def test_failed_launch_rolls_back_the_worktree(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the PTY path so a bad command raises FileNotFoundError -> ValueError
    # inside supervisor.start() (the tmux path would spawn-then-fail differently).
    monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: False)
    store = HistoryStore(tmp_path / "db.sqlite")
    wt_root = tmp_path / "wt"
    orch = Orchestrator(EventBus(sink=store.record), worktrees=WorktreeManager(root=wt_root))

    async def scenario() -> None:
        with pytest.raises(ValueError):
            await orch.launch(
                runtime=GenericRuntime("this-command-does-not-exist-xyz"),
                repo_path=str(git_repo),
                branch="doomed",
                session_key="doomed",
            )

    asyncio.run(scenario())

    # No worktree directory left on disk.
    leftover = list(wt_root.glob("*")) if wt_root.exists() else []
    assert leftover == [], f"orphaned worktree(s): {leftover}"

    # No dangling supervisor entry.
    assert orch.get("doomed") is None

    # The branch was not left behind in the repo.
    import subprocess

    branches = subprocess.run(
        ["git", "-C", str(git_repo), "branch", "--list", "doomed"],
        capture_output=True,
        text=True,
    ).stdout
    assert "doomed" not in branches


def test_successful_launch_after_a_failed_one_reuses_the_branch_name(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Because the failed launch rolled its branch back, the same branch name is
    free to use again — proving the rollback actually released it."""
    import sys

    fake_agent = Path(__file__).parent.parent / "fakes" / "fake_agent.py"
    store = HistoryStore(tmp_path / "db.sqlite")
    orch = Orchestrator(
        EventBus(sink=store.record), worktrees=WorktreeManager(root=tmp_path / "wt")
    )

    async def scenario() -> str:
        monkeypatch.setattr(orch_mod.tmux, "has_tmux", lambda: False)
        with pytest.raises(ValueError):
            await orch.launch(
                runtime=GenericRuntime("nope-not-a-real-binary"),
                repo_path=str(git_repo),
                branch="retry-me",
                session_key="first",
            )
        # Same branch name must be available again (rollback deleted it).
        key = await orch.launch(
            runtime=GenericRuntime(f"{sys.executable} {fake_agent}"),
            repo_path=str(git_repo),
            branch="retry-me",
            session_key="second",
        )
        await asyncio.wait_for(orch.get(key)._task, 5)  # type: ignore[union-attr,arg-type]
        return key

    key = asyncio.run(scenario())
    row = store.session(key)
    assert row is not None
    assert row["branch"] == "retry-me"
