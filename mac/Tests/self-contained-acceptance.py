#!/usr/bin/env python3
"""Acceptance test for a self-contained DuckTerm app bundle.

    python3 mac/Tests/self-contained-acceptance.py "/path/to/DuckTerm Lite.app" --edition lite

Copies the bundle into a directory whose path has spaces, then drives its
Contents/Resources/bin/duckterm wrapper with no Homebrew, pipx, Python or tmux
on PATH and with a hostile Python environment (PYTHONHOME, PYTHONPATH pointing
at a fake `duckterm`, VIRTUAL_ENV, PYTHONSTARTUP, PYTHONUSERBASE). Everything
runs in a private home, port and tmux socket, sessions are test:true, and all
of it is removed afterwards. The original bundle is only read.

Checks: CLI version, server edition, a terminal session and its pane log, the
Shell helper, agent PATH, hooks after a second app location, a restart that
keeps the session, a refused second server, and no writes into the bundle
(file hashes and `codesign --verify`).
"""

import argparse
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
import urllib.error
import urllib.request
from pathlib import Path

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def manifest(app: Path) -> dict[str, str]:
    files = {}
    for path in sorted(app.rglob("*")):
        if path.is_file() and not path.is_symlink():
            files[str(path.relative_to(app))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(
    base: str, token: str, method: str, path: str, body: object = None
) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("X-Duckterm-Token", token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def wait_for(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.3)
    return False


def serve(wrapper: Path, edition: str, env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    with log.open("wb") as err:  # the child keeps its own copy of the descriptor
        return subprocess.Popen(
            [str(wrapper), "serve", "--edition", edition],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--edition", choices=["lite", "code", "full"], required=True)
    parser.add_argument(
        "--control",
        action="store_true",
        help="omit the hostile Python variables, to tell env failures from others",
    )
    args = parser.parse_args()
    source = args.app.resolve()
    version = plistlib.loads((source / "Contents/Info.plist").read_bytes())[
        "CFBundleShortVersionString"
    ]

    root = Path(tempfile.mkdtemp(prefix="duckterm-sc-"))
    app = root / "Dir With Spaces" / source.name
    second = root / "Another Place" / source.name
    home = root / "home"
    work = root / "work"
    shadow = root / "shadow"
    socket_name = f"sc-accept-{os.getpid()}"
    port = free_port()
    server: subprocess.Popen[bytes] | None = None
    try:
        app.parent.mkdir(parents=True)
        subprocess.run(["ditto", str(source), str(app)], check=True)
        before = manifest(app)
        signed = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True
        )
        check(
            "bundle signature verifies before use",
            signed.returncode == 0,
            signed.stderr.decode().strip(),
        )

        # A fake duckterm package and startup hooks that must never be loaded.
        (shadow / "duckterm/helpers").mkdir(parents=True)
        (shadow / "duckterm/__init__.py").write_text(
            "raise SystemExit('SHADOW duckterm imported')\n"
        )
        (shadow / "duckterm/helpers/__init__.py").write_text("")
        startup_marker = root / "pythonstartup-ran"
        (shadow / "startup.py").write_text(f"open({str(startup_marker)!r}, 'w').close()\n")
        home.mkdir()
        work.mkdir()
        subprocess.run(["git", "init", "-q", str(work)], check=True)

        wrapper = app / "Contents/Resources/bin/duckterm"
        env = {
            "HOME": str(home),
            "USER": os.environ.get("USER", "qa"),
            "TMPDIR": str(root),
            "TERM": "xterm-256color",
            # As the native app starts its server: the bundle's CLI first, no Homebrew.
            "PATH": f"{app / 'Contents/Resources/bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONHOME": "/nonexistent-python-home",
            "PYTHONPATH": str(shadow),
            "VIRTUAL_ENV": "/nonexistent-venv",
            "PYTHONSTARTUP": str(shadow / "startup.py"),
            "PYTHONUSERBASE": str(shadow),
            "DUCKTERM_HOME": str(home / ".duckterm"),
            "DUCKTERM_PORT": str(port),
            "DUCKTERM_TMUX_SOCKET": socket_name,
            "DUCKTERM_NO_BROWSER": "1",
            "DUCKTERM_RELEASE_CHECK": "off",
        }
        if args.control:
            for name in (
                "PYTHONHOME",
                "PYTHONPATH",
                "VIRTUAL_ENV",
                "PYTHONSTARTUP",
                "PYTHONUSERBASE",
            ):
                env.pop(name)

        run = subprocess.run([str(wrapper), "--version"], env=env, capture_output=True, text=True)
        check(
            "CLI wrapper runs from a path with spaces under a hostile env",
            run.returncode == 0 and run.stdout.strip() == version,
            (run.stdout + run.stderr).strip(),
        )

        server = serve(wrapper, args.edition, env, root / "server.log")
        base = f"http://127.0.0.1:{port}"
        token_file = home / ".duckterm/token"
        up = wait_for(
            lambda: token_file.exists()
            and request(base, token_file.read_text().strip(), "GET", "/sessions")[0] == 200,
            30,
        )
        check(
            "server starts without external Python or tmux",
            up,
            (root / "server.log").read_text(errors="replace")[-400:],
        )
        if not up:
            return 1
        token = token_file.read_text().strip()
        status, edition = request(base, token, "GET", "/edition")
        check(
            "server reports the requested edition",
            status == 200 and isinstance(edition, dict) and edition.get("id") == args.edition,
            str(edition),
        )

        status, launched = request(
            base,
            token,
            "POST",
            "/sessions/launch",
            {
                "command": "sh",
                "cwd": str(work),
                "in_terminal": False,
                "test": True,
                "name": "sc-accept",
            },
        )
        key = launched.get("session_key") if isinstance(launched, dict) else None
        check(
            "terminal session launches with bundled tmux",
            status == 200 and bool(key),
            str(launched),
        )
        if not key:
            return 1

        probe = root / "pane-probe.json"
        marker = "sc-accept-pane-marker"
        line = (
            'printf \'{"duckterm":"%s","pythonpath":"%s","pythonhome":"%s","version":"%s",'
            '"jq":"%s","jq_ok":"%s"}\' '
            '"$(command -v duckterm)" "${PYTHONPATH-}" "${PYTHONHOME-}" '
            '"$(duckterm --version 2>&1)" "${DUCKTERM_BUNDLED_JQ-}" '
            '"$(echo 1 | "${DUCKTERM_BUNDLED_JQ:-false}" -c . 2>&1)" '
            f"> '{probe}'; echo {marker}\n"
        )
        request(base, token, "POST", f"/sessions/{key}/input", {"text": line})
        wait_for(probe.exists, 15)
        seen = json.loads(probe.read_text()) if probe.exists() else {}
        resources = str(app / "Contents/Resources")
        check(
            "agents find this bundle's duckterm first on PATH",
            seen.get("duckterm") == str(wrapper),
            str(seen.get("duckterm")),
        )
        check(
            "agent CLI matches the bundle version",
            seen.get("version") == version,
            str(seen.get("version")),
        )
        # macOS 15 ships /usr/bin/jq, so only the named bundled path proves anything.
        check(
            "agents get this bundle's jq for hook approvals and notices",
            seen.get("jq", "").startswith(resources) and seen.get("jq_ok") == "1",
            f"{seen.get('jq')} -> {seen.get('jq_ok')}",
        )
        check(
            "no bundle path injected into agent PYTHONPATH/PYTHONHOME",
            resources not in seen.get("pythonpath", "")
            and resources not in seen.get("pythonhome", ""),
            str(seen),
        )

        # tmux pipe-pane runs the pane_log helper with the tmux server's env.
        logs = home / ".duckterm/panes"
        logged = wait_for(
            lambda: any(
                marker in p.read_text(errors="replace") for p in logs.rglob("*") if p.is_file()
            ),
            15,
        )
        check("pane log helper records output despite the hostile env", logged)

        status, shell = request(base, token, "POST", f"/sessions/{key}/shell", {})
        time.sleep(2)
        status2, shell_state = request(base, token, "GET", f"/sessions/{key}/shell")
        check(
            "Shell helper opens and stays up",
            status == 200 and isinstance(shell_state, dict) and shell_state.get("open") is True,
            f"{shell} / {shell_state}",
        )

        hook_env = {k: v for k, v in env.items() if k not in {"DUCKTERM_PORT"}}
        run = subprocess.run(
            [str(wrapper), "install-hooks", "--global", "--agent", "claude-code"],
            env=hook_env,
            capture_output=True,
            text=True,
        )
        settings = home / ".claude/settings.json"
        commands = []
        if settings.exists():
            hooks = json.loads(settings.read_text()).get("hooks", {})
            commands = [
                h["command"] for e in hooks.get("PostToolUse", []) for h in e.get("hooks", [])
            ]
        check(
            "hooks install and point into this bundle",
            run.returncode == 0 and len(commands) == 1 and resources in commands[0],
            f"{run.stderr.strip()} {commands}",
        )

        # The same app at a second location (moved, or another edition's app).
        second.parent.mkdir(parents=True)
        subprocess.run(["ditto", str(app), str(second)], check=True)
        run = subprocess.run(
            [
                str(second / "Contents/Resources/bin/duckterm"),
                "install-hooks",
                "--global",
                "--agent",
                "claude-code",
            ],
            env=hook_env,
            capture_output=True,
            text=True,
        )
        hooks = json.loads(settings.read_text()).get("hooks", {}) if settings.exists() else {}
        commands = [h["command"] for e in hooks.get("PostToolUse", []) for h in e.get("hooks", [])]
        check(
            "reinstalling hooks from another app location leaves exactly one hook per event",
            run.returncode == 0 and len(commands) == 1,
            str(commands),
        )

        # Restart: the server stops, tmux keeps the pane, the session is re-adopted.
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=20)
        server = serve(wrapper, args.edition, env, root / "server2.log")
        back = wait_for(lambda: request(base, token, "GET", "/sessions")[0] == 200, 30)
        status, sessions = request(base, token, "GET", "/sessions")
        rows = sessions.get("sessions", sessions) if isinstance(sessions, dict) else sessions or []
        row = next((r for r in rows if r.get("session_key") == key), None)
        check(
            "restart keeps the session and its live pane",
            back and row is not None and row.get("state") not in {"terminated", "stopped"},
            str(row and row.get("state")),
        )

        other = "full" if args.edition != "full" else "lite"
        dup = subprocess.run(
            [str(wrapper), "serve", "--edition", other],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        status, edition = request(base, token, "GET", "/edition")
        check(
            "a second server for the same home is refused and the edition is unchanged",
            dup.returncode != 0 and isinstance(edition, dict) and edition.get("id") == args.edition,
            dup.stderr.strip()[-200:],
        )

        check("PYTHONSTARTUP was never executed", not startup_marker.exists())
        after = manifest(app)
        changed = sorted(
            set(before) ^ set(after) | {p for p in before if p in after and before[p] != after[p]}
        )
        check("nothing inside the bundle was written", not changed, ", ".join(changed[:5]))
        signed = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True
        )
        check(
            "bundle signature still verifies after use",
            signed.returncode == 0,
            signed.stderr.decode().strip(),
        )
    finally:
        if server is not None and server.poll() is None:
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
        # Whichever tmux the bundle ships, wherever it keeps it.
        tmuxes = [str(p) for p in app.rglob("tmux") if p.is_file() and os.access(p, os.X_OK)]
        for binary in [*tmuxes, shutil.which("tmux")]:
            if binary:
                subprocess.run([binary, "-L", socket_name, "kill-server"], capture_output=True)
        # Pane shells write their history as tmux kills them; let them finish first.
        time.sleep(1)
        shutil.rmtree(root, ignore_errors=True)
        Path(f"/private/tmp/tmux-{os.getuid()}/{socket_name}").unlink(missing_ok=True)
    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
