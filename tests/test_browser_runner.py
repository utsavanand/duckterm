"""Exercise isolation and teardown through the runner's actual process boundary."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("outcome", ["success", "failure", "terminate"])
def test_browser_runner_isolates_home_and_cleans_after_exit(tmp_path: Path, outcome: str) -> None:
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    runner = root / "scripts/run_browser_tests.py"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/run_browser_tests.py", runner)
    executable = root / "web/node_modules/.bin/playwright"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import os,sys,json,time\nfrom pathlib import Path\n"
        "home=Path(os.environ['HOME']); (home/'.claude').mkdir()\n"
        "(home/'.claude/transcript.jsonl').write_text('synthetic')\n"
        "Path(os.environ['RD_TEST_STATE_FILE']).write_text('owned')\n"
        "keys=['HOME','RD_TEST_RUN_ROOT','RD_TEST_STATE_FILE','RD_TEST_PORT',"
        "'RD_TEST_TMUX_SOCKET','PLAYWRIGHT_BROWSERS_PATH','CLAUDE_CONFIG_DIR','CODEX_HOME']\n"
        "Path(sys.argv[2]).write_text(json.dumps({k:os.environ.get(k) for k in keys}))\n"
        "if sys.argv[3]=='terminate':\n while True:time.sleep(.1)\n"
        "sys.exit(7 if sys.argv[3]=='failure' else 0)\n"
    )
    executable.chmod(0o755)
    capture = tmp_path / "environment.json"
    process = subprocess.Popen(
        [sys.executable, str(runner), str(capture), outcome],
        env={
            **os.environ,
            "CLAUDE_CONFIG_DIR": "/not-a-test-root",
            "CODEX_HOME": "/not-a-test-root",
        },
    )
    try:
        deadline = time.monotonic() + 10
        while not capture.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert capture.exists()
        if outcome == "terminate":
            process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == {"success": 0, "failure": 7, "terminate": 143}[outcome]
        environment = json.loads(capture.read_text())
        assert environment["HOME"] != str(Path.home())
        assert environment["CLAUDE_CONFIG_DIR"] is None
        assert environment["CODEX_HOME"] is None
        assert environment["RD_TEST_TMUX_SOCKET"].startswith("rd-e2e-")
        assert 0 < int(environment["RD_TEST_PORT"]) < 65536
        assert Path(environment["RD_TEST_STATE_FILE"]).parent == Path(
            environment["RD_TEST_RUN_ROOT"]
        )
        assert not Path(environment["RD_TEST_RUN_ROOT"]).exists()
        assert environment["PLAYWRIGHT_BROWSERS_PATH"] == os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH",
            str(
                (
                    Path.home() / "Library/Caches"
                    if sys.platform == "darwin"
                    else Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
                )
                / "ms-playwright"
            ),
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
