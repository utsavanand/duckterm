"""Real locks and Unix sockets: orphan recovery must not touch an active run."""

import os
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "scripts/test_socket_lease.py"


@pytest.fixture
def isolated(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="lease-", dir="/tmp") as d:
        monkeypatch.setenv("TMUX_TMPDIR", d)
        yield Path(d)


def test_live_lease_blocks_sweeper(isolated):
    code = runpy.run_path(str(MODULE))
    with code["lease"]() as name:
        root = isolated / f"tmux-{os.getuid()}"
        root.mkdir()
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(root / name))
        p = subprocess.run(
            [sys.executable, str(MODULE)], capture_output=True, text=True, check=True
        )
        assert name in p.stdout
        assert (root / name).exists()  # Even a dead socket belongs to the active run.
    assert not (root / name).exists()


def test_unregistered_and_production_preserved(isolated):
    root = isolated / f"tmux-{os.getuid()}"
    root.mkdir()
    names = ["duckterm", "duckterm-videodemo", "duckterm-videoremote", "duckterm-pytest-123"]
    for name in names:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(root / name))
    assert runpy.run_path(str(MODULE))["sweep"]() == {"removed": [], "preserved": []}
    assert sorted(p.name for p in root.iterdir()) == sorted(names)


@pytest.mark.skipif(not shutil.which("tmux"), reason="tmux required")
@pytest.mark.parametrize("occupied", [False, True, "client"])
def test_killed_owner_empty_only_recovery(isolated, occupied):
    # The lease-owning process dies without a finally block (SIGKILL).
    script = f"""
import runpy,subprocess,time
m=runpy.run_path({str(MODULE)!r})
with m['lease']() as name:
 subprocess.run(['tmux','-L',name,'new-session','-d','-s','fixture','sleep 120'],check=True)
 subprocess.run(['tmux','-L',name,'set-option','-s','exit-empty','off'],check=True)
 if {occupied!r} is not True:
  subprocess.run(['tmux','-L',name,'kill-session','-t','fixture'],check=True)
 print(name,flush=True)
 time.sleep(120)
"""
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    name = proc.stdout.readline().strip()
    client = None
    try:
        assert name.startswith("duckterm-pytest-")
        proc.kill()
        proc.wait(timeout=5)
        if occupied == "client":
            client = subprocess.Popen(["tmux", "-L", name, "wait-for", "qa134-hold"])
            deadline = time.monotonic() + 5
            while True:
                count = subprocess.run(
                    ["tmux", "-L", name, "display-message", "-p", "#{L:1}"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                if count.stdout.strip() == "11":
                    break
                assert time.monotonic() < deadline
                time.sleep(0.02)
        result = runpy.run_path(str(MODULE))["sweep"]()
        path = isolated / f"tmux-{os.getuid()}" / name
        if occupied:
            assert name in result["preserved"]
            assert path.exists()
            p = subprocess.run(["tmux", "-L", name, "list-sessions"], capture_output=True)
            assert p.returncode == 0 if occupied is True else client.poll() is None
        else:
            assert not path.exists(), (
                result,
                [
                    subprocess.run(["tmux", "-L", name, c], capture_output=True, text=True)
                    for c in ("list-sessions", "list-clients")
                ],
            )
            assert not (runpy.run_path(str(MODULE))["registry"]() / name).exists()
    finally:
        if client is not None:
            client.terminate()
            client.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if name:
            subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True)
            runpy.run_path(str(MODULE))["sweep"]()


def test_symlink_registry_refused(isolated):
    target = isolated / "other"
    target.mkdir()
    (isolated / f"duckterm-test-leases-{os.getuid()}").symlink_to(target)
    with pytest.raises(ValueError):
        runpy.run_path(str(MODULE))["sweep"]()


def test_clients_and_unknown_errors_preserved(monkeypatch):
    code = runpy.run_path(str(MODULE))
    for rc, out, err in [
        (0, "sessions=;clients=11;pid=123\n", ""),
        (0, "sessions=1;clients=;pid=123\n", ""),
        (0, "", ""),
        (1, "", "permission denied"),
    ]:

        def command(args, rc=rc, out=out, err=err, **kwargs):
            return subprocess.CompletedProcess(args, rc, out, err)

        monkeypatch.setattr(subprocess, "run", command)
        assert not code["empty"](Path("/unused"))
