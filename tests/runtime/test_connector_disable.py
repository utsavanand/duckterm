"""A live stdio connection closes on disable and stale configs cannot reconnect."""

import json
import os
import subprocess
import sys
from pathlib import Path

from duckterm import connectors


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
