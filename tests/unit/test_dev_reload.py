from duckterm.dev_reload import _source_mtimes, changed_path


def test_source_mtimes_includes_the_package_modules() -> None:
    paths = _source_mtimes()
    assert any(p.endswith("dev_reload.py") for p in paths)
    assert any(p.endswith("server.py") for p in paths)


def test_changed_path_none_when_unchanged() -> None:
    snap = {"a.py": 1.0, "b.py": 2.0}
    assert changed_path(snap, dict(snap)) is None
    # An empty snapshot is also "no change" — guards against treating the first
    # poll (nothing seen yet) as a reload trigger.
    assert changed_path({}, {}) is None


def test_changed_path_detects_a_modified_file() -> None:
    before = {"a.py": 1.0, "b.py": 2.0}
    after = {"a.py": 1.0, "b.py": 2.5}  # b.py touched
    assert changed_path(before, after) == "b.py"


def test_changed_path_detects_a_new_file() -> None:
    before = {"a.py": 1.0}
    after = {"a.py": 1.0, "c.py": 3.0}
    assert changed_path(before, after) == "c.py"


def test_changed_path_detects_a_deleted_file() -> None:
    before = {"a.py": 1.0, "b.py": 2.0}
    after = {"a.py": 1.0}
    assert changed_path(before, after) == "b.py"


def test_reload_retains_embedded_interpreter_flags(monkeypatch):
    import pytest

    from duckterm import dev_reload

    original = ["python", "-I", "-B", "-m", "duckterm.cli", "serve", "--reload"]
    monkeypatch.setattr(dev_reload.sys, "orig_argv", original)
    monkeypatch.setattr(dev_reload.time, "sleep", lambda _: None)
    monkeypatch.setattr(dev_reload, "_source_mtimes", lambda: {"server.py": 2.0})
    captured = []

    def reexec(executable, arguments):
        captured.append((executable, arguments))
        raise InterruptedError("reexec observed")

    monkeypatch.setattr(dev_reload.os, "execv", reexec)
    with pytest.raises(InterruptedError, match="reexec observed"):
        dev_reload._watch({"server.py": 1.0})
    assert captured == [(dev_reload.sys.executable, [dev_reload.sys.executable, *original[1:]])]
