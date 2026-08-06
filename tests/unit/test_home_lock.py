"""The DUCKTERM_HOME pidfile guard: a second server refuses to start if a LIVE
server already owns the home (the silent-corruption case — shared DB + tmux
panes), but a stale pidfile from a crashed server is reclaimed."""

import os

import pytest

from duckterm.server import _acquire_home_lock, _release_home_lock


def test_stale_pidfile_is_reclaimed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    # A crashed server left a pidfile naming a pid that's almost certainly dead.
    (tmp_path / "server.pid").write_text("999999")
    lock = _acquire_home_lock()  # must not raise — reclaim it
    assert int(lock.read_text()) == os.getpid()
    _release_home_lock(lock)
    assert not lock.exists()


def test_live_owner_blocks_a_second_server(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    # A live process owns the home: use THIS process's pid, which is definitely
    # alive but not us-at-acquire-time... so simulate a different live pid by
    # spawning a sleeper.
    import subprocess

    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        (tmp_path / "server.pid").write_text(str(sleeper.pid))
        with pytest.raises(SystemExit, match="already owns"):
            _acquire_home_lock()
    finally:
        sleeper.terminate()
        sleeper.wait()


def test_acquire_then_release_roundtrip(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    lock = _acquire_home_lock()
    assert lock.exists() and int(lock.read_text()) == os.getpid()
    _release_home_lock(lock)
    assert not lock.exists()


def test_release_does_not_delete_a_lock_another_server_reclaimed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    lock = _acquire_home_lock()
    # A replacement server reclaimed the pidfile (wrote its own pid).
    lock.write_text("424242")
    _release_home_lock(lock)  # ours-check fails → must NOT delete it
    assert lock.exists() and lock.read_text() == "424242"
