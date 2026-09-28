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
