"""New panes use this backend's app tools, not ones frozen in an older tmux server."""

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from duckterm.agents import tmux

TOOLS = ("DUCKTERM_BUNDLED_CLI", "DUCKTERM_BUNDLED_JQ", "DUCKTERM_BUNDLED_TMUX")
SYSTEM_TMUX = shutil.which("tmux")
pytestmark = pytest.mark.skipif(SYSTEM_TMUX is None, reason="tmux is not installed")


@pytest.fixture
def stale_server(monkeypatch, tmp_path):
    """A private server started by an 'old app' whose bundle paths no longer exist."""
    binary = os.path.realpath(SYSTEM_TMUX)
    socket = "duckterm-test-pane-env-" + uuid.uuid4().hex[:12]
    monkeypatch.setenv("DUCKTERM_TMUX_SOCKET", socket)
    stale = {name: f"/Volumes/Old/DuckTerm.app/{name}" for name in TOOLS}
    subprocess.run(
        [binary, "-L", socket, "-f", "/dev/null", "new-session", "-d", "-s", "rd_old", "/bin/cat"],
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            **({"TMUX_TMPDIR": os.environ["TMUX_TMPDIR"]} if "TMUX_TMPDIR" in os.environ else {}),
            **stale,
        },
        check=True,
    )
    tmux._bundled_selection.cache_clear()
    path = None
    try:
        path = subprocess.run(
            [binary, "-L", socket, "display-message", "-p", "#{socket_path}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        yield binary, socket, stale
    finally:
        subprocess.run([binary, "-L", socket, "kill-server"], capture_output=True)
        # kill-server can leave the socket file behind.
        if path:
            Path(path).unlink(missing_ok=True)
        tmux._bundled_selection.cache_clear()


def pids(binary, socket, target):
    return subprocess.run(
        [binary, "-L", socket, "display-message", "-p", "-t", target, "#{pid}:#{pane_pid}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def pane_env(path: Path) -> dict[str, str]:
    for _ in range(100):
        if path.exists() and path.read_text().endswith("END\n"):
            break
        time.sleep(0.05)
    lines = path.read_text().splitlines()[:-1]
    return dict(line.split("=", 1) for line in lines if "=" in line)


def record(path: Path) -> str:
    return f"env > {path}; echo END >> {path}; exec /bin/cat"


def test_current_app_tools_override_stale_server_environment(stale_server, monkeypatch, tmp_path):
    binary, socket, stale = stale_server
    before = pids(binary, socket, "rd_old")
    # Stale values are really in the server's global environment.
    globals_ = subprocess.run(
        [binary, "-L", socket, "show-environment", "-g"], capture_output=True, text=True
    ).stdout
    assert all(f"{name}={value}" in globals_ for name, value in stale.items())

    current = {
        "DUCKTERM_BUNDLED_CLI": str(tmp_path / "New DuckTerm.app/bin/duckterm"),
        "DUCKTERM_BUNDLED_JQ": str(tmp_path / "New DuckTerm.app/jq/bin/jq"),
        "DUCKTERM_BUNDLED_TMUX": binary,
    }
    for name, value in current.items():
        monkeypatch.setenv(name, value)
    assert tmux.selected_client() == (binary, "bundled")

    tmux.spawn("plain", record(tmp_path / "plain.env"), str(tmp_path))
    plain = pane_env(tmp_path / "plain.env")
    # Private launches carry their secrets in a script, not tmux arguments.
    tmux.spawn_piped(
        "private",
        f"env > {tmp_path / 'private.env'}; echo END >> {tmp_path / 'private.env'}",
        str(tmp_path),
        str(tmp_path / "private.log"),
        {"DUCKTERM_SESSION_TOKEN": "secret-value"},
        private_env=True,
    )
    private = pane_env(tmp_path / "private.env")

    for seen in (plain, private):
        assert {name: seen.get(name) for name in TOOLS} == current
    assert private["DUCKTERM_SESSION_TOKEN"] == "secret-value"
    assert pids(binary, socket, "rd_old") == before
    assert pids(binary, socket, "rd_plain").split(":")[0] == before.split(":")[0]


def test_backend_without_app_tools_drops_stale_paths(stale_server, monkeypatch, tmp_path):
    binary, socket, _ = stale_server
    before = pids(binary, socket, "rd_old")
    for name in TOOLS:
        monkeypatch.delenv(name, raising=False)
    # Source and pipx installs keep PATH tmux and no selection probe.
    assert tmux.selected_client() == ("tmux", "system")

    tmux.spawn("source", record(tmp_path / "source.env"), str(tmp_path))
    seen = pane_env(tmp_path / "source.env")

    assert not any(name in seen for name in TOOLS)
    assert pids(binary, socket, "rd_old") == before
    assert pids(binary, socket, "rd_source").split(":")[0] == before.split(":")[0]
