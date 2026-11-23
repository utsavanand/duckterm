"""Signals must run owned-resource cleanup without killing live agents."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest


def wait_for(path: Path, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, f"Timed out waiting for {path.name}"
        time.sleep(0.02)


@pytest.mark.parametrize(
    "signum,repeated", [(signal.SIGTERM, False), (signal.SIGTERM, True), (signal.SIGINT, False)]
)
def test_server_signal_reaps_tunnel_and_preserves_agent(tmp_path: Path, signum, repeated):
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "DUCKTERM_HOME": str(tmp_path / "state"),
        "DUCKTERM_TMUX_SOCKET": "shutdown-" + uuid.uuid4().hex,
        "DUCKTERM_EDITION": "full",
        "DUCKTERM_NO_KEYCHAIN": "1",
        "DUCKTERM_SUMMARIZER": "off",
        "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }
    agent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    server = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("server_shutdown_worker.py"))],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    tunnel_pid = None
    try:
        wait_for(tmp_path / "ready.json", server)
        stall_log = tmp_path / "state" / "server-stalls.log"
        assert not stall_log.exists()
        tunnel_pid = json.loads((tmp_path / "ready.json").read_text())["tunnel_pid"]
        server.send_signal(signum)
        wait_for(tmp_path / "closing", server)
        if repeated:
            server.send_signal(signal.SIGTERM)
            time.sleep(0.1)
            assert server.poll() is None, "Second SIGTERM interrupted cleanup"
        (tmp_path / "finish-close").touch()
        stdout, stderr = server.communicate(timeout=10)
        assert server.returncode == 0, stdout + stderr
        with pytest.raises(ProcessLookupError):
            os.kill(tunnel_pid, 0)
        tunnel_pid = None
        assert agent.poll() is None, "Shutdown stopped an unrelated agent process"
    finally:
        for process in (server, agent):
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        if tunnel_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(tunnel_pid, signal.SIGKILL)


@pytest.mark.parametrize("mode", ["child_ignores_term", "mobile_stall", "executor_stall"])
def test_shutdown_is_bounded_when_cleanup_stalls(tmp_path: Path, mode):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DUCKTERM_")}
    env.update(
        {
            "HOME": str(tmp_path),
            "DUCKTERM_HOME": str(tmp_path / "state"),
            "DUCKTERM_TMUX_SOCKET": "shutdown-" + uuid.uuid4().hex,
            "DUCKTERM_EDITION": "full",
            "DUCKTERM_NO_BROWSER": "1",
            "DUCKTERM_NO_KEYCHAIN": "1",
            "DUCKTERM_SUMMARIZER": "off",
            "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "TEST_SHUTDOWN_MODE": mode,
        }
    )
    agent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    server = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("server_shutdown_worker.py"))],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    tunnel_pid = None
    try:
        wait_for(tmp_path / "ready.json", server)
        tunnel_pid = json.loads((tmp_path / "ready.json").read_text())["tunnel_pid"]
        started = time.monotonic()
        server.terminate()
        stdout, stderr = server.communicate(timeout=12)
        assert time.monotonic() - started < 10, stdout + stderr
        assert server.returncode == (1 if mode == "executor_stall" else 0), stdout + stderr
        with pytest.raises(ProcessLookupError):
            os.kill(tunnel_pid, 0)
        tunnel_pid = None
        assert agent.poll() is None
        if mode == "executor_stall":
            assert "shutdown deadline exceeded" in stderr
    finally:
        for process in (server, agent):
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        if tunnel_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(tunnel_pid, signal.SIGKILL)


def test_restart_recovers_from_legacy_server_stuck_after_listener_close(tmp_path: Path):
    import urllib.error
    import urllib.request

    env = {k: v for k, v in os.environ.items() if not k.startswith("DUCKTERM_")}
    env.update(
        {
            "HOME": str(tmp_path),
            "DUCKTERM_HOME": str(tmp_path / "state"),
            "DUCKTERM_TMUX_SOCKET": "restart-" + uuid.uuid4().hex,
            "DUCKTERM_EDITION": "full",
            "DUCKTERM_NO_BROWSER": "1",
            "DUCKTERM_NO_KEYCHAIN": "1",
            "DUCKTERM_SUMMARIZER": "off",
            "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "TEST_SHUTDOWN_MODE": "legacy_stall",
        }
    )
    command = [sys.executable, str(Path(__file__).with_name("server_shutdown_worker.py"))]
    old = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    replacement = None
    tunnel_pids = []
    try:
        ready = tmp_path / "ready.json"
        wait_for(ready, old)
        first = json.loads(ready.read_text())
        tunnel_pids.append(first["tunnel_pid"])
        env.update(
            TEST_DO_RESTART="1",
            TEST_RESTART_PORT=str(first["port"]),
            TEST_SHUTDOWN_MODE="child_ignores_term",
        )
        replacement = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        deadline = time.monotonic() + 20
        while True:
            old.poll()  # Reap it so the restart process sees that its PID is gone.
            assert replacement.poll() is None, replacement.communicate()
            if ready.exists() and (current := json.loads(ready.read_text()))["pid"] != old.pid:
                break
            assert time.monotonic() < deadline, "Restart never restored its listener"
            time.sleep(0.02)
        tunnel_pids.append(current["tunnel_pid"])
        assert old.returncode == -signal.SIGKILL
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(f"http://127.0.0.1:{first['port']}/", timeout=2)
        except urllib.error.HTTPError as error:
            response = error  # The authenticated dashboard can answer 401.
        with response:
            assert response.headers.get("X-Duckterm") == "1"
        replacement.terminate()
        _, stderr = replacement.communicate(timeout=10)
        assert replacement.returncode == 0, stderr
        for pid in tunnel_pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        tunnel_pids.clear()
    finally:
        for process in (old, replacement):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
        for pid in tunnel_pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
