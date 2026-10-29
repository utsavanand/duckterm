"""Bundled tmux keeps existing system-tmux panes usable, on private sockets only.

Usage: python mac/Tests/bundled-tmux.py /abs/bundled/tmux /abs/system/tmux

The backend runs with only /usr/bin:/bin on PATH, as a Finder-launched app does.
It must select the bundled client, adopt a pane a system tmux server already
owns without restarting it, drive and stream that pane, and start a fresh
server from the bundle when none exists. Every fixture server is killed.
"""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

bundle = str(Path(sys.argv[1]).resolve())
system = str(Path(sys.argv[2]).resolve())
run_id = uuid.uuid4().hex[:12]
existing = "duckterm-test-adopt-" + run_id
fresh = "duckterm-test-fresh-" + run_id

PROBE = r"""
import asyncio, json, os
from duckterm.agents import tmux, tmux_stream

async def first_snapshot(target):
    feed = tmux_stream.stream(target)
    try:
        return bytes(await asyncio.wait_for(anext(feed), 10)).decode(errors="replace")
    finally:
        await feed.aclose()

result = {"client": tmux.selected_client(), "has_tmux": tmux.has_tmux()}
if os.environ["PROBE"] == "existing":
    target = "rd_adopt"
    result["live"] = tmux.list_duckterm_sessions()
    result["sent"] = tmux.send_keys(target, "typed-through-bundle")
    result["snapshot"] = asyncio.run(first_snapshot(target))
    tmux.spawn("adopt2", "/bin/cat", "/")
else:
    target = tmux.spawn("fresh", "/bin/cat", "/")
ok, pids = tmux._tmux("display-message", "-p", "-t", target, "#{pid}:#{pane_pid}")
result["pids"] = pids.strip()
result["sessions"] = sorted(tmux.session_targets())
print(json.dumps(result))
"""


PIDS = ("display-message", "-p", "-t", "rd_adopt", "#{pid}:#{pane_pid}")


def tmux(binary, socket, *args):
    return subprocess.run(
        [binary, "-L", socket, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def executable(pid):
    return subprocess.run(
        ["ps", "-o", "comm=", "-p", pid], capture_output=True, text=True, check=True
    ).stdout.strip()


def backend(home, socket, probe):
    env = {
        "HOME": home,
        "PATH": "/usr/bin:/bin",
        "DUCKTERM_HOME": str(Path(home) / ".duckterm"),
        "DUCKTERM_TMUX_SOCKET": socket,
        "DUCKTERM_BUNDLED_TMUX": bundle,
        "PROBE": probe,
    }
    result = subprocess.run(
        [sys.executable, "-c", PROBE], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1]), result.stderr


with tempfile.TemporaryDirectory() as home:
    try:
        tmux(system, existing, "-f", "/dev/null", "new-session", "-d", "-s", "rd_adopt", "/bin/cat")
        before = tmux(system, existing, *PIDS)
        system_version = tmux(system, existing, "-V")
        adopted, log = backend(home, existing, "existing")
        assert adopted["client"] == [bundle, "bundled"], adopted
        assert f"tmux client: bundled {bundle}" in log, log
        assert adopted["live"] == ["adopt"], adopted
        assert adopted["sent"] and "typed-through-bundle" in adopted["snapshot"], adopted
        assert adopted["pids"] == before, (before, adopted)
        assert adopted["sessions"] == ["rd_adopt", "rd_adopt2"], adopted
        server_pid = before.split(":")[0]
        assert executable(server_pid) == system, executable(server_pid)
        after = tmux(system, existing, *PIDS)
        assert after == before, (before, after)
        print(f"PASS existing {system_version} server {before} kept, adopted by bundled client")

        started, _ = backend(home, fresh, "fresh")
        assert started["client"] == [bundle, "bundled"], started
        fresh_server = started["pids"].split(":")[0]
        assert executable(fresh_server) == bundle, executable(fresh_server)
        print(f"PASS bundled {tmux(bundle, fresh, '-V')} started fresh server {fresh_server}")
    finally:
        for socket in (existing, fresh):
            subprocess.run([system, "-L", socket, "kill-server"], capture_output=True)
        for socket in (existing, fresh):
            alive = subprocess.run([system, "-L", socket, "list-sessions"], capture_output=True)
            assert alive.returncode != 0, f"fixture server {socket} survived cleanup"
            # tmux can leave the socket file behind after kill-server.
            (Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}" / socket).unlink(
                missing_ok=True
            )
