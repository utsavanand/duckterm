"""Run a relocated app with an empty home and no Python/CLI on PATH.

Requires macOS GUI access. Uses the real app/ServerProcess and bundled backend;
only the copied bundle's test identity/instance change. No production data used.
"""

import hashlib
import json
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from contextlib import suppress
from pathlib import Path

source = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix="duckterm-bundle-smoke-") as scratch:
    root = Path(scratch)
    home = root / "empty-home"
    home.mkdir()
    app = root / "Relocated App.app"
    shutil.copytree(source, app, symlinks=True)
    contents = app / "Contents"
    instance = "bundle-" + uuid.uuid4().hex[:12]
    port = 4310 + int.from_bytes(hashlib.sha256(instance.encode()).digest()[:4], "big") % 600
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
    info_path = contents / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info.update(
        CFBundleIdentifier="com.duckterm.smoke." + instance,
        DucktermTestBuild=True,
        DucktermTestSystemTools=False,
        DucktermTestInstance=instance,
        DucktermTestPort=port,
    )
    info_path.write_bytes(plistlib.dumps(info))
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "TMPDIR": scratch,
        "PYTHONHOME": "/does-not-exist",
        "PYTHONPATH": "/does-not-exist",
    }
    wrapper = contents / "Resources/bin/duckterm"
    version = subprocess.check_output([str(wrapper), "--version"], env=env, text=True).strip()
    # A symlink is enough for optional external CLI access; do not alter the real home.
    (home / ".local/bin").mkdir(parents=True)
    link = home / ".local/bin/duckterm"
    link.symlink_to(wrapper)
    assert subprocess.check_output([str(link), "--version"], env=env, text=True).strip() == version
    link.unlink()  # first launch must not depend on an installed CLI
    native = subprocess.Popen(
        [str(contents / "MacOS/DuckTerm")],
        env=env,
        cwd=scratch,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    children = []
    bundled_tmux = str(contents / "Resources/tmux/bin/tmux")
    tmux_socket = "duckterm-" + instance
    try:
        for _ in range(100):
            if native.poll() is not None:
                raise RuntimeError(f"Native app exited: {native.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                    assert response.status == 200 and response.headers.get("X-Duckterm") == "1"
                    assert b"<html" in response.read().lower()
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Bundled server did not start")
        rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,command="], text=True)
        children = [
            int(line.split(None, 2)[0])
            for line in rows.splitlines()
            if len(line.split(None, 2)) == 3 and line.split(None, 2)[1] == str(native.pid)
        ]
        assert any(
            "Resources/python/bin/python3.13" in line
            and "duckterm.cli serve" in line
            and line.split(None, 2)[1] == str(native.pid)
            for line in rows.splitlines()
            if len(line.split(None, 2)) == 3
        ), "Native process did not launch bundled Python"
        assert (home / (".duckterm-" + instance) / "db.sqlite").is_file()
        state_home = home / (".duckterm-" + instance)
        token = (state_home / "token").read_text().strip()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/sessions/launch",
            data=json.dumps(
                {
                    "command": "/bin/cat",
                    "cwd": scratch,
                    "name": "bundle-smoke-test",
                    "test": True,
                    "in_terminal": False,
                }
            ).encode(),
            headers={"Content-Type": "application/json", "X-Duckterm-Token": token},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            key = json.load(response)["session_key"]
        # Resolve the instance's socket scheme from the bundled helper.
        probe_env = dict(
            env, PYTHONPATH=str(contents / "Resources/backend"), DUCKTERM_INSTANCE=instance
        )
        probe_env.pop("PYTHONHOME")
        tmux_socket = subprocess.check_output(
            [
                str(contents / "Resources/python/bin/python3.13"),
                "-s",
                "-B",
                "-c",
                "from duckterm.helpers.instance import tmux_socket; print(tmux_socket())",
            ],
            env=probe_env,
            text=True,
        ).strip()
        pane = subprocess.check_output(
            [
                bundled_tmux,
                "-L",
                tmux_socket,
                "display-message",
                "-p",
                "-t",
                "rd_" + key,
                "#{pane_pid}",
            ],
            env=env,
            text=True,
        ).strip()
        assert pane.isdigit(), "Session did not launch under bundled tmux"
        import sqlite3

        with sqlite3.connect(state_home / "db.sqlite") as db:
            payloads = [
                json.loads(row[0])
                for row in db.execute("SELECT payload_json FROM events WHERE session_key=?", (key,))
            ]
        assert any(
            p.get("tmux_source") == "bundled" and p.get("tmux_binary") == bundled_tmux
            for p in payloads
        )

        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        print(
            json.dumps(
                {
                    "version": version,
                    "dashboard": 200,
                    "bundled_child": True,
                    "relocated": True,
                    "empty_home": True,
                    "signature_unchanged": True,
                    "bundled_tmux_session": True,
                    "system_tools_disabled": True,
                }
            )
        )
    finally:
        # Collect any startup child even if the readiness assertion failed.
        rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid="], text=True)
        children += [
            int(line.split()[0])
            for line in rows.splitlines()
            if len(line.split()) == 2 and line.split()[1] == str(native.pid)
        ]
        native.terminate()
        try:
            native.wait(timeout=10)
        except subprocess.TimeoutExpired:
            native.kill()
            native.wait()
        subprocess.run(
            [bundled_tmux, "-L", tmux_socket, "kill-server"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for pid in set(children):
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        subprocess.run(
            ["/usr/bin/defaults", "delete", info["CFBundleIdentifier"]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
