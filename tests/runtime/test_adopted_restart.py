"""Prove the composed server replacement -> adoption -> model restart workflow."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_adopted_claude_preserves_identity_messages_and_model(tmp_path: Path) -> None:
    tmux = shutil.which("tmux")
    if not tmux:
        pytest.skip("tmux required for real adoption")
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "tmux").symlink_to(tmux)
    fake = binary / "claude"
    fake.write_text(
        "#!" + sys.executable + "\n"
        "import sys,time,json,os\nfrom pathlib import Path\n"
        "if '--version' in sys.argv:\n print('synthetic Claude');sys.exit(0)\n"
        "with (Path(os.environ['HOME'])/'argv.jsonl').open('a') as f:"
        "f.write(json.dumps(sys.argv)+'\\n')\n"
        "print('────────────────\\n❯ \\n────────────────',flush=True)\n"
        "while True:time.sleep(1)\n"
    )
    fake.chmod(0o755)
    namespace = "adoption-" + tmp_path.name
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "DUCKTERM_HOME": str(tmp_path / "state"),
        "DUCKTERM_TMUX_SOCKET": namespace,
        "PATH": str(binary) + ":/usr/bin:/bin",
        "SHELL": "/bin/sh",
        "DUCKTERM_SUMMARIZER": "off",
        "DUCKTERM_NO_TERMINAL": "1",
        "DUCKTERM_NO_KEYCHAIN": "1",
        "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }
    try:
        for phase in ("seed", "restart"):
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("adopted_restart_worker.py")), phase],
                env=env,
                capture_output=True,
                text=True,
                timeout=35,
            )
            assert result.returncode == 0, result.stdout + result.stderr
    finally:
        subprocess.run([tmux, "-L", namespace, "kill-server"], capture_output=True)
