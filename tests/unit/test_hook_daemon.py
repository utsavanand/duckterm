"""The hook under Codex 0.159's shared app-server daemon (design "Shared-daemon
identity", 2026-10-01). There the hook inherits the environment of whichever
session started the daemon, so it must not assert DUCKTERM_SESSION_KEY or
report the daemon's pid; the agent's own session_id carries identity."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

HOOK = Path(__file__).parents[2] / "src/duckterm/hooks/duckterm-hook.sh"
# Codex 0.159.3 hook payload (probe, 2026-10-01; transcript_path trimmed).
PAYLOAD = {
    "session_id": "01a0f8d2-b119-77d2-88c7-92ff73c3f056",
    "turn_id": "01a0f8d2-3389-7fa3-bd88-85061f87ce77",
    "cwd": "/tmp/work",
    "prompt": "Run the shell command: echo B. Then reply ok.",
}


def run_hook(tmp_path, *, daemon, env):
    if not shutil.which("jq"):
        pytest.skip("jq unavailable")
    binary = tmp_path / "bin"
    binary.mkdir(exist_ok=True)
    capture = tmp_path / "curl-args"
    curl = binary / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    curl.chmod(0o700)
    # The hook's parent: Codex's daemon, or the agent's own process.
    parent = tmp_path / "codex"
    parent.write_text(f'#!/bin/sh\nbash "{HOOK}" UserPromptSubmit codex\n')
    parent.chmod(0o700)
    argv = (
        [str(parent), "app-server", "--listen", "unix://", "--managed-daemon"]
        if daemon
        else [str(parent)]
    )
    subprocess.run(
        argv,
        input=json.dumps(PAYLOAD),
        text=True,
        check=True,
        env={
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "HOME": str(tmp_path),
            "DUCKTERM_HOME": str(tmp_path),
            "CAPTURE": str(capture),
            **env,
        },
    )
    deadline = time.time() + 5  # the event post runs in the background
    while not capture.exists() and time.time() < deadline:
        time.sleep(0.05)
    args = json.loads(capture.read_text())
    url = next(a for a in args if a.startswith("http"))
    return url, json.loads(args[args.index("-d") + 1])


def test_under_the_daemon_the_hook_sends_only_the_agents_own_id(tmp_path) -> None:
    stale = {"DUCKTERM_SESSION_KEY": "session-that-started-the-daemon"}
    _, sent = run_hook(tmp_path, daemon=True, env=stale)
    assert sent["session_id"] == PAYLOAD["session_id"]
    assert sent["hook_host"] == "daemon"
    assert "session_key" not in sent and "agent_pid" not in sent


def test_a_per_process_agent_keeps_env_identity(tmp_path) -> None:
    _, sent = run_hook(tmp_path, daemon=False, env={"DUCKTERM_SESSION_KEY": "launched-key"})
    assert sent["session_key"] == "launched-key"
    assert isinstance(sent["agent_pid"], int)
    assert "hook_host" not in sent


@pytest.mark.parametrize(
    ("env_url", "file_url", "expected"),
    [
        ("http://127.0.0.1:5001", "http://127.0.0.1:5002", "http://127.0.0.1:5001"),
        (None, "http://127.0.0.1:5002", "http://127.0.0.1:5002"),
        (None, None, "http://127.0.0.1:4300"),
    ],
)
def test_url_comes_from_env_then_the_instance_file(tmp_path, env_url, file_url, expected) -> None:
    if file_url:
        (tmp_path / ".duckterm").mkdir()
        (tmp_path / ".duckterm" / "instance-url").write_text(file_url + "\n")
    env = {"DUCKTERM_URL": env_url} if env_url else {}
    url, _ = run_hook(tmp_path, daemon=True, env=env)
    assert url == expected + "/events"
