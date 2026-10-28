"""The hook prefers the app's bundled jq, so clean Macs keep approvals and notices."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[3] / "src/duckterm/hooks/duckterm-hook.sh"
STOP_OUTPUT = {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "Inbox"}}


def executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o700)
    return path


@pytest.fixture
def rig(tmp_path: Path):
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq unavailable")
    # Answers like the server: events, approval registration, then a decision.
    executable(
        tmp_path / "fakes/curl",
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "url = next(a for a in sys.argv if a.startswith('http'))\n"
        "if url.endswith('/decision'):\n"
        "    print(json.dumps({'status': 'approve'}))\n"
        "elif url.endswith('/approvals'):\n"
        "    print(json.dumps({'id': 'r1'}))\n"
        "else:\n"
        f"    print(json.dumps({{'hook_output': {STOP_OUTPUT!r}}}))\n",
    )
    # A jq on PATH that always fails: correct output proves the bundled one ran.
    executable(tmp_path / "broken/jq", "#!/bin/sh\nexit 3\n")
    executable(tmp_path / "pathjq/jq", f'#!/bin/sh\nexec "{jq}" "$@"\n')
    marker = tmp_path / "bundled-jq-ran"
    bundled = executable(
        tmp_path / "Dir With Spaces/jq/bin/jq", f'#!/bin/sh\n: > "{marker}"\nexec "{jq}" "$@"\n'
    )
    (tmp_path / "token").write_text("t")
    # Nothing inherited: no session key, and a URL no server listens on.
    env = {
        "PATH": f"{tmp_path / 'fakes'}:{tmp_path / 'broken'}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "DUCKTERM_HOME": str(tmp_path),
        "DUCKTERM_URL": "http://127.0.0.1:9",
        "DUCKTERM_BUNDLED_JQ": str(bundled),
    }
    return env, marker, tmp_path


def run_hook(env: dict[str, str], event: str) -> str:
    result = subprocess.run(
        ["bash", str(SCRIPT), event, "claude-code"],
        input='{"session_id":"s","tool_name":"Bash","stop_hook_active":false}',
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_stop_notice_uses_bundled_jq_over_path(rig) -> None:
    env, marker, _ = rig
    assert json.loads(run_hook(env, "Stop")) == STOP_OUTPUT
    assert marker.exists()


def test_blocking_approval_uses_bundled_jq_over_path(rig) -> None:
    env, marker, _ = rig
    decision = json.loads(run_hook(env, "PermissionRequest"))
    assert decision["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    assert marker.exists()


def test_unusable_bundled_jq_falls_back_to_path(rig) -> None:
    env, marker, root = rig
    env["PATH"] = f"{root / 'fakes'}:{root / 'pathjq'}:/usr/bin:/bin"
    env["DUCKTERM_BUNDLED_JQ"] = str(root / "missing/jq")
    assert json.loads(run_hook(env, "Stop")) == STOP_OUTPUT
    assert not marker.exists()
