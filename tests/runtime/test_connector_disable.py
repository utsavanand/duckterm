"""A live stdio connection closes on disable and stale configs cannot reconnect."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from duckterm import connectors


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _alive_group(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.parametrize("action", ["disable", "terminate"])
def test_shutdown_stops_credential_holding_descendant(
    tmp_path: Path, monkeypatch, action: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / ".duckterm"))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    heartbeat = tmp_path / "heartbeat"
    pids = tmp_path / "pids.json"
    child = (
        "import signal,time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"p=Path({str(heartbeat)!r})\n"
        "while True:\n p.write_text(str(time.monotonic_ns()))\n time.sleep(.02)\n"
    )
    binary = tmp_path / "github-mcp-server"
    # Record the server's own pid, its process group, and the credential
    # holder's pid. The supervisor is not a group leader, so the test cannot
    # derive the group that run() signals — it has to be told.
    binary.write_text(
        f"#!{sys.executable}\nimport json,os,subprocess,sys,time\n"
        f"held = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"open({str(pids)!r},'w').write(json.dumps("
        "{'server': os.getpid(), 'group': os.getpgid(0), 'holder': held.pid}))\n"
        "time.sleep(60)\n"
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    connectors.save_secret("github", "test-only")
    connectors._save_policy("github", {"enabled": True, "source": "stored", "generation": "a"})
    proc = subprocess.Popen([sys.executable, "-m", "duckterm.cli", "connector-run", "github"])
    try:
        deadline = time.monotonic() + 5
        while not (heartbeat.exists() and pids.exists()) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert heartbeat.exists() and pids.exists()
        recorded = json.loads(pids.read_text())
        if action == "disable":
            connectors.disable("github", home=tmp_path)
        else:
            proc.terminate()
        proc.wait(timeout=5)
        # The credential holder is what must not survive, and it is a
        # grandchild: run() signals the MCP server's group, whose id is the
        # server's pid, not the supervisor's. Wait for the holder itself to
        # go, then prove it wrote nothing after that.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(recorded["holder"]):
            time.sleep(0.02)
        assert not _alive(recorded["holder"]), (
            f"credential holder {recorded['holder']} outlived run(); "
            f"server={recorded['server']} group={recorded['group']} "
            f"group_alive={_alive_group(recorded['group'])}"
        )
        before = heartbeat.stat().st_mtime_ns
        time.sleep(0.15)
        assert heartbeat.stat().st_mtime_ns == before
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        # A survivor would otherwise idle for 60s and contaminate later tests.
        if pids.exists():
            for pid in json.loads(pids.read_text()).values():
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)


def test_disable_terminates_live_mcp_and_blocks_stale_shim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / ".duckterm"))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    binary = tmp_path / "github-mcp-server"
    binary.write_text(
        f"#!{sys.executable}\nimport sys\nprint('ready', flush=True)\n"
        "for line in sys.stdin: print(line, end='', flush=True)\n"
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    connectors.save_secret("github", "test-only")
    connectors._save_policy("github", {"enabled": True, "source": "stored", "generation": "a"})
    proc = subprocess.Popen(
        [sys.executable, "-m", "duckterm.cli", "connector-run", "github"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
        connectors.disable("github", home=tmp_path)
        proc.wait(timeout=5)
        assert connectors.load_secret("github") == "test-only"
        stale = subprocess.run(
            [sys.executable, "-m", "duckterm.cli", "connector-run", "github"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert stale.returncode != 0
        assert "disabled" in stale.stderr
        assert "test-only" not in json.dumps(connectors.status("github", home=tmp_path))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
