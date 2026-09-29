import runpy
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "scripts/cleanup_test_sockets.py"


@pytest.fixture
def short_root():
    with tempfile.TemporaryDirectory(prefix="qasc-", dir="/tmp") as path:
        yield Path(path)


@pytest.mark.parametrize(
    "error,live,removed",
    [
        ("no server running on fixture", False, True),
        ("Permission denied", False, False),
        ("no server running on fixture", True, False),
    ],
)
def test_only_confirmed_dead_socket_removed(short_root, monkeypatch, error, live, removed):
    tmp_path = short_root
    import os

    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    root = tmp_path / f"tmux-{os.getuid()}"
    root.mkdir()
    test = root / "duckterm-pytest-123"
    production = root / "duckterm"
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(test))
    if live:
        sock.listen()
    else:
        sock.close()
    production.write_text("untouched")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", error)
    )
    try:
        result = runpy.run_path(str(MODULE))["cleanup"]()
        assert (test.name in result["removed"]) == removed
        assert test.exists() != removed
        assert production.read_text() == "untouched"
    finally:
        sock.close()


def test_owned_cannot_name_production():
    with pytest.raises(ValueError):
        runpy.run_path(str(MODULE))["cleanup"]("duckterm")


@pytest.mark.parametrize("kill_succeeds", [True, False])
def test_owned_kills_only_exact_namespace_and_hyphen_children(
    short_root, monkeypatch, kill_succeeds
):
    import os

    monkeypatch.setenv("TMUX_TMPDIR", str(short_root))
    root = short_root / f"tmux-{os.getuid()}"
    root.mkdir()
    owned = "duckterm-pytest-123"
    names = [owned, owned + "-empty", owned + "4", "duckterm", "rd-e2e-other"]
    sockets = {}
    for name in names:
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(root / name))
        sock.listen()
        sockets[name] = sock
    killed = []

    def command(args, **kwargs):
        name = Path(args[2]).name
        if args[3] == "kill-server":
            killed.append(name)
            if kill_succeeds:
                sockets[name].close()
            return subprocess.CompletedProcess(args, 0 if kill_succeeds else 1, "", "")
        return subprocess.CompletedProcess(args, 1, "", "no server running on fixture")

    monkeypatch.setattr(subprocess, "run", command)
    try:
        result = runpy.run_path(str(MODULE))["cleanup"](owned)
        assert set(killed) == {owned, owned + "-empty"}
        assert set(result["removed"]) == ({owned, owned + "-empty"} if kill_succeeds else set())
        for name in names:
            assert (root / name).exists() == (not kill_succeeds or name not in killed)
    finally:
        for sock in sockets.values():
            sock.close()


def test_replaced_inode_is_preserved(short_root, monkeypatch):
    import os

    monkeypatch.setenv("TMUX_TMPDIR", str(short_root))
    root = short_root / f"tmux-{os.getuid()}"
    root.mkdir()
    path = root / "duckterm-pytest-123"
    old = socket.socket(socket.AF_UNIX)
    old.bind(str(path))
    replacement = socket.socket(socket.AF_UNIX)

    def replace_during_probe(args, **kwargs):
        path.unlink()
        replacement.bind(str(path))
        replacement.close()
        return subprocess.CompletedProcess(args, 1, "", "no server running on fixture")

    monkeypatch.setattr(subprocess, "run", replace_during_probe)
    try:
        result = runpy.run_path(str(MODULE))["cleanup"]()
        assert result["removed"] == []
        assert result["preserved"] == [path.name]
        assert path.exists()
    finally:
        old.close()
        replacement.close()
