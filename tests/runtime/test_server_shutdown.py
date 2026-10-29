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
