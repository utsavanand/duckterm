"""The Mac app's CLI names its app profile; Python decides whether to apply it."""

import os
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).parents[2] / "mac/Resources/duckterm-cli"
TARGETS = (
    "DUCKTERM_DESKTOP_PROFILE",
    "DUCKTERM_INSTANCE",
    "DUCKTERM_HOME",
    "DUCKTERM_PORT",
    "DUCKTERM_URL",
    "DUCKTERM_TMUX_SOCKET",
    "DUCKTERM_SESSION_KEY",
)


@pytest.fixture
def app_cli(tmp_path):
    """A stamped wrapper in an app layout whose 'python' prints its environment."""
    resources = tmp_path / "Lite Test.app/Contents/Resources"
    (resources / "bin").mkdir(parents=True)
    (resources / "python/bin").mkdir(parents=True)
    cli = resources / "bin/duckterm"
    cli.write_text(WRAPPER.read_text().replace("@DESKTOP_PROFILE@", "test-lite"))
    python = resources / "python/bin/python3.13"
    python.write_text("#!/bin/sh\nenv\n")
    for path in (cli, python):
        path.chmod(0o755)
    return cli


def run(cli: Path, **extra: str) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), **extra}
    out = subprocess.run([str(cli)], env=env, capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def test_names_the_stamped_app_profile(app_cli):
    seen = run(app_cli)
    assert seen["DUCKTERM_APP_PROFILE"] == "test-lite"
    assert "DUCKTERM_DESKTOP_PROFILE" not in seen


@pytest.mark.parametrize("target", TARGETS)
def test_explicit_target_and_enrollment_reach_python_unchanged(app_cli, target):
    # The shell never parses credentials or chooses a target itself.
    seen = run(app_cli, **{target: "chosen"})
    assert seen[target] == "chosen"
    assert seen["DUCKTERM_APP_PROFILE"] == "test-lite"
