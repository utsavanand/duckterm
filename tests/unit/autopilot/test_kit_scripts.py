"""The autopilot kit's tests and verdict scripts, run as the flow runs them:
JSON on stdin, one JSON line out."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).parents[3] / "src/duckterm/autopilot/kits/autopilot/scripts"


def _script(name: str, inputs: dict[str, Any], cwd: Path) -> dict[str, Any]:
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / name)],
        input=json.dumps(inputs),
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    )
    return dict(json.loads(out.stdout.splitlines()[-1]))


@pytest.mark.parametrize(("code", "choice"), [(0, "pass"), (3, "fail")])
def test_tests_pass_or_fail_on_the_commands_exit_code(
    tmp_path: Path, code: int, choice: str
) -> None:
    (tmp_path / "t.py").write_text(f"print('ran the suite'); raise SystemExit({code})\n")
    command = f"{sys.executable} t.py"
    done = _script("tests.py", {"settings": json.dumps({"test_command": command})}, tmp_path)
    assert done["choice"] == choice
    assert done["reason"] == f"`{command}` exited {code}"
    assert "ran the suite" in done["outputs"]["log"]


def test_tests_never_go_through_a_shell(tmp_path: Path) -> None:
    command = f"{sys.executable} -c pass; touch {tmp_path / 'pwned'}"
    _script("tests.py", {"settings": json.dumps({"test_command": command})}, tmp_path)
    assert not (tmp_path / "pwned").exists()


def test_no_test_command_passes_and_says_so(tmp_path: Path) -> None:
    done = _script("tests.py", {"settings": "{}"}, tmp_path)
    assert (done["choice"], done["reason"]) == ("pass", "no test command set")


@pytest.mark.parametrize(
    ("verdicts", "choice"),
    [
        ({"qa": "pass", "security": "approve"}, "ship"),
        ({"qa": "fail", "security": "approve"}, "revise"),
        ({"qa": "pass", "security": "revise"}, "revise"),
        ({"qa": "pass", "security": "reject"}, "stop"),
        ({"qa": None, "security": "approve"}, "revise"),
        ({}, "revise"),
    ],
)
def test_the_verdict_ships_only_when_every_reviewer_passed(
    tmp_path: Path, verdicts: dict[str, Any], choice: str
) -> None:
    assert _script("verdict.py", verdicts, tmp_path)["choice"] == choice
