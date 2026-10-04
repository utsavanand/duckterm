import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "slop_check.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("slop_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checks_files_in_a_worktree_under_the_duckterm_home(tmp_path, monkeypatch) -> None:
    """DuckTerm's worktrees live under ~/.duckterm/worktrees; skipping by the
    absolute path made the check pass there without reading a file."""
    checker = load_checker()
    repo = tmp_path / ".duckterm" / "worktrees" / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    assert f() is not None\n")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "test_skip.py").write_text("x = 1\n")
    monkeypatch.setattr(checker, "ROOT", repo)
    assert [p.name for p in checker.iter_files((".py",))] == ["test_x.py"]
    assert checker.main() == 1  # the existence-only test is caught
