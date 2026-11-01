"""Run a relocated app with an empty home and no Python/CLI on PATH.

Requires macOS GUI access and a Test build (mac/build.sh --test [--edition=...]).
Uses the real app/ServerProcess, bundled backend and the build's own test profile,
in a private HOME and TMUX_TMPDIR. Only the copied bundle's ID changes, so its
preferences stay apart from an installed Test app. No production data used.
"""

import json
import os
import plistlib
import pwd
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
# Preferences and WebKit storage follow the real account home and bundle ID,
# whatever $HOME says. A unique copy ID keeps them apart; prove it and clean up.
LIBRARY = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library"


def storage(bundle_id: str) -> dict[str, tuple[int, float]]:
    """Every per-bundle store macOS or WebKit keeps: file count and latest change."""
    found = {}
    for path in (
        LIBRARY / "Preferences" / f"{bundle_id}.plist",
        LIBRARY / "WebKit" / bundle_id,
        LIBRARY / "Caches" / bundle_id,
        LIBRARY / "HTTPStorages" / bundle_id,
        LIBRARY / "HTTPStorages" / f"{bundle_id}.binarycookies",
        LIBRARY / "Saved Application State" / f"{bundle_id}.savedState",
    ):
        if path.exists():
            files = [path, *path.rglob("*")] if path.is_dir() else [path]
            found[str(path)] = (len(files), max(f.lstat().st_mtime for f in files))
    return found


# Socket paths are limited to 104 bytes, so tmux gets a short private directory.
with (
    tempfile.TemporaryDirectory(prefix="duckterm-bundle-smoke-") as scratch,
    tempfile.TemporaryDirectory(prefix="dt-tmux-", dir="/tmp") as tmux_dir,
):
    root = Path(scratch)
    home = root / "empty-home"
    home.mkdir()
    app = root / "Relocated App.app"
    shutil.copytree(source, app, symlinks=True)
    contents = app / "Contents"
    info_path = contents / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    if info.get("DucktermTestBuild") is not True:
        sys.exit("Build with mac/build.sh --test; only Test builds honor a private HOME")
    port = info["DucktermPort"]
    try:
        with socket.socket() as sock:
            # As the server binds: closing connections must not read as in use.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
    except OSError:
        sys.exit(f"Port {port} is in use; quit {info['CFBundleName']} or its server first")
    state_home = home / info["DucktermHome"]
    real_id = info["CFBundleIdentifier"]
    real_before = storage(real_id)
    tmux_socket = info["DucktermTmuxSocket"]
    info.update(
        CFBundleIdentifier=info["CFBundleIdentifier"] + ".smoke." + uuid.uuid4().hex[:12],
        DucktermTestSystemTools=False,
    )
    info_path.write_bytes(plistlib.dumps(info))
    copy_id = info["CFBundleIdentifier"]
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "TMPDIR": scratch,
        "TMUX_TMPDIR": tmux_dir,
        "PYTHONHOME": "/does-not-exist",
        "PYTHONPATH": "/does-not-exist",
        "DUCKTERM_NO_KEYCHAIN": "1",
        "DUCKTERM_SUMMARIZER": "off",
        "DUCKTERM_RELEASE_CHECK": "off",
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
        assert (state_home / "db.sqlite").is_file()
        token = (state_home / "token").read_text().strip()
        edition = urllib.request.Request(
            f"http://127.0.0.1:{port}/edition", headers={"X-Duckterm-Token": token}
        )
        with urllib.request.urlopen(edition, timeout=5) as response:
            identity = json.load(response)["instance"]
        assert identity["profile_id"] == info["DucktermProfile"], identity
        assert Path(identity["home"]) == state_home.resolve(), identity
        assert (identity["tmux_socket"], identity["port"]) == (tmux_socket, port), identity
        marker = json.loads((state_home / "profile.json").read_text())
        assert marker["profile_id"] == info["DucktermProfile"], marker
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
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        print(
            json.dumps(
                {
                    "version": version,
                    "dashboard": 200,
                    "bundled_child": True,
                    "profile": info["DucktermProfile"],
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
        used = storage(copy_id)
        subprocess.run(
            ["/usr/bin/defaults", "delete", copy_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for path in map(Path, used):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        isolation = {
            "copy_storage_used": sorted(p.replace(str(LIBRARY), "~/Library") for p in used),
            "copy_storage_removed": storage(copy_id) == {},
            "real_test_app_storage_unchanged": storage(real_id) == real_before,
        }
        print(json.dumps(isolation))
        if not (isolation["copy_storage_removed"] and isolation["real_test_app_storage_unchanged"]):
            sys.exit("Test storage was not isolated")
