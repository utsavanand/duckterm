#!/usr/bin/env python3
"""Acceptance test for DuckTerm editions installed and running side by side.

    python3 mac/Tests/parallel-editions-acceptance.py \
        "/path/DuckTerm Test.app" "/path/DuckTerm Lite Test.app" "/path/DuckTerm Code Test.app"

Each bundle is ditto-copied under a path with spaces and driven through its own
Contents/Resources/bin/duckterm with DUCKTERM_DESKTOP_PROFILE set from its
Info.plist, the way the native app starts its server. Everything runs under a
temporary HOME and a private TMUX_TMPDIR, so no real ~/.duckterm*, ~/.claude
or per-user tmux socket is touched: profile tmux socket names are per user, not
per HOME. Profiles that need a busy port (the canonical Full profile needs 4300)
are refused up front. Sessions are test:true; servers, sockets and files are
removed afterwards and the cleanup is asserted.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

FAILURES: list[str] = []
SITE = "Contents/Resources/python/lib/python3.13/site-packages/duckterm"
# Info.plist key -> resolver field. Key names to be confirmed with the native build.
MANIFEST = {
    "DucktermProfile": "profile_id",
    "DucktermEdition": "edition",
    "DucktermInstance": "instance_id",
    "DucktermHomeName": "home_name",
    "DucktermTmuxSocket": "tmux_socket",
    "DucktermPort": "port",
    "CFBundleIdentifier": "bundle_id",
    "CFBundleDisplayName": "app_name",
}


def bundle_state(app: Path) -> tuple[bool, str]:
    """The bundle's signature status and a hash over every file in it."""
    import hashlib

    signed = (
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True
        ).returncode
        == 0
    )
    digest = hashlib.sha256()
    for path in sorted(app.rglob("*")):
        if path.is_file() and not path.is_symlink():
            digest.update(str(path.relative_to(app)).encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return signed, digest.hexdigest()


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def request(base: str, token: str, method: str, path: str, body: object = None):
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


def port_busy(port: int) -> bool:
    import socket

    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


class App:
    """One copied bundle, its contract profile and its running server."""

    # One tmux client copied aside before any app is removed, so every socket can
    # still be inspected and stopped after its own bundle is gone.
    tmux_binary: Path | None = None

    def __init__(self, source: Path, root: Path, env: dict[str, str]) -> None:
        self.source = source
        self.app = root / "Dir With Spaces" / source.name
        subprocess.run(["ditto", str(source), str(self.app)], check=True)
        info = plistlib.loads((self.app / "Contents/Info.plist").read_bytes())
        self.info = info
        self.edition = info.get("DucktermEdition", "full")
        self.test = bool(info.get("DucktermTestBuild"))
        self.bundle_id = info["CFBundleIdentifier"]
        self.app_name = info.get("CFBundleDisplayName", "")
        self.cli = self.app / "Contents/Resources/bin/duckterm"
        self.python = self.app / "Contents/Resources/python/bin/python3.13"
        contract = [str(self.python), "-I", str(self.app / SITE / "helpers/instance.py")]
        contract += ["--desktop-profile", self.edition] + (["--test"] if self.test else [])
        out = subprocess.run(contract, capture_output=True, text=True)
        self.profile = json.loads(out.stdout) if out.returncode == 0 else {}
        # The complete context the native app passes: profile plus its instance.
        self.env = {**env, "DUCKTERM_DESKTOP_PROFILE": self.profile.get("profile_id", "")}
        if self.profile.get("instance_id"):
            self.env["DUCKTERM_INSTANCE"] = self.profile["instance_id"]
        self.home = Path(env["HOME"]) / self.profile.get("home_name", ".missing")
        self.base = f"http://127.0.0.1:{self.profile.get('port')}"
        self.server: subprocess.Popen[bytes] | None = None
        self.log = root / f"{self.profile.get('profile_id', 'unknown')}.log"

    @property
    def token(self) -> str:
        path = self.home / "token"
        return path.read_text().strip() if path.exists() else ""

    def start(self) -> bool:
        with self.log.open("ab") as err:
            self.server = subprocess.Popen(
                [str(self.cli), "serve", "--edition", self.edition],
                env=self.env,
                stdout=subprocess.DEVNULL,
                stderr=err,
            )
        return wait_for(lambda: bool(self.token) and self.get("/sessions")[0] == 200, 40)

    def stop(self) -> bool:
        """Stop and reap the server; True once the child has certainly exited."""
        if self.server is None:
            return True
        if self.server.poll() is None:
            self.server.send_signal(signal.SIGTERM)
            try:
                self.server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=10)
        return self.server.returncode is not None

    def run_python(self, code: str) -> tuple[int, str]:
        """Run code in this bundle's own interpreter with this profile's context."""
        out = subprocess.run(
            [str(self.python), "-I", "-c", code], env=self.env, capture_output=True, text=True
        )
        return out.returncode, (out.stdout if out.returncode == 0 else out.stderr).strip()

    def get(self, path: str):
        return request(self.base, self.token, "GET", path)

    def post(self, path: str, body: object):
        return request(self.base, self.token, "POST", path, body)

    def tmux(self, *args: str) -> subprocess.CompletedProcess[str]:
        binary = App.tmux_binary or self.app / "Contents/Resources/tmux/bin/tmux"
        return subprocess.run(
            [str(binary), "-L", self.profile.get("tmux_socket", "missing"), *args],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def events(self, key: str) -> list[dict]:
        db = self.home / "db.sqlite"
        if not db.exists():
            return []
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM events WHERE session_key = ?", (key,)).fetchall()
        return [dict(r) for r in rows]


def launch(app: App, work: Path, name: str, **extra: object) -> str | None:
    status, body = app.post(
        "/sessions/launch",
        {"command": "sh", "in_terminal": False, "test": True, "name": name, **extra},
    )
    return body.get("session_key") if status == 200 and isinstance(body, dict) else None


def pane_run(app: App, key: str, script: str, out: Path) -> dict:
    """Run a shell line inside the session's pane; it writes JSON to `out`."""
    app.post(f"/sessions/{key}/input", {"text": script + "\n"})
    wait_for(out.exists, 20)
    try:
        return json.loads(out.read_text())
    except (OSError, ValueError):
        return {}


def snapshot(path: Path) -> dict[str, str]:
    import hashlib

    if path.is_symlink():
        return {".": "symlink:" + os.readlink(path)}
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def ownership_cases(app: App) -> None:
    """An existing workspace the app didn't create is refused and left as it was."""
    pid = app.profile["profile_id"]
    other = app.home.parent / "someone-elses-workspace"
    cases = {
        "an unmarked existing home": lambda: (
            app.home.mkdir(),
            (app.home / "db.sqlite").write_bytes(b"user data"),
            (app.home / "token").write_text("user-token\n"),
        ),
        "a home marked for another profile": lambda: (
            app.home.mkdir(),
            (app.home / "profile.json").write_text(
                json.dumps({"format": 1, "profile_id": "other", "edition": app.edition}) + "\n"
            ),
        ),
        "a symlinked home": lambda: (
            other.mkdir(),
            (other / "db.sqlite").write_bytes(b"user data"),
            app.home.symlink_to(other),
        ),
    }
    for label, make in cases.items():
        make()
        before, outside = snapshot(app.home), snapshot(other) if other.exists() else {}
        run = subprocess.run(
            [str(app.cli), "serve", "--edition", app.edition],
            env=app.env,
            capture_output=True,
            text=True,
            timeout=40,
        )
        check(
            f"{pid}: {label} is refused and left unchanged",
            run.returncode != 0
            and not port_busy(int(app.profile["port"]))
            and snapshot(app.home) == before
            and (snapshot(other) if other.exists() else {}) == outside,
            run.stderr.strip()[-200:],
        )
        if app.home.is_symlink():
            app.home.unlink()
        shutil.rmtree(app.home, ignore_errors=True)
        shutil.rmtree(other, ignore_errors=True)


MEMBERS = (
    "import json; from pathlib import Path; from duckterm.agents import hook_dispatch as h; "
    "print(json.dumps(sorted(h.registered_profiles("
    "Path.home() / '.claude/settings.json', 'claude-code', legacy=False))))"
)


def our_commands(settings: Path) -> dict[str, list[list[str]]]:
    """Per event, the argv of every hook command shaped like ours."""
    import shlex

    hooks = json.loads(settings.read_text()).get("hooks", {}) if settings.exists() else {}
    found: dict[str, list[list[str]]] = {}
    for event, groups in hooks.items():
        for group in groups:
            for hook in group.get("hooks", []):
                argv = shlex.split(hook.get("command", ""))
                if len(argv) == 3 and argv[0].endswith("duckterm-hook.sh"):
                    found.setdefault(event, []).append(argv)
    return found


def symlinked_home_install(app: App, root: Path) -> None:
    """A home reached through a symlinked ancestor (relocated home folders, and
    every macOS temp dir via /var -> /private/var) must still accept hooks."""
    real = root / "relocated" / "user"
    real.mkdir(parents=True)
    link = root / "home-link"
    link.symlink_to(real.parent)
    env = {**app.env, "HOME": str(link / "user")}
    run = subprocess.run(
        [str(app.cli), "install-hooks", "--global", "--agent", "claude-code"],
        env=env,
        capture_output=True,
        text=True,
    )
    check(
        "install-hooks works when HOME has a symlinked ancestor",
        run.returncode == 0 and bool(our_commands(real / ".claude/settings.json")),
        run.stderr.strip()[-160:],
    )
    link.unlink()
    shutil.rmtree(root / "relocated", ignore_errors=True)


def shared_hooks(apps: list[App], root: Path, keys: dict[str, str]) -> None:
    home = Path(apps[0].env["HOME"])
    settings = home / ".claude/settings.json"
    shared = home / ".duckterm-hooks/v1"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(json.dumps({"model": "qa-keep"}) + "\n")

    profile_keys = {}
    for app in apps:
        run = subprocess.run(
            [str(app.cli), "install-hooks", "--global", "--agent", "claude-code"],
            env=app.env,
            capture_output=True,
            text=True,
        )
        rc, key = app.run_python(
            "from duckterm.agents.hook_dispatch import profile_key; print(profile_key())"
        )
        profile_keys[app.profile["profile_id"]] = key if rc == 0 else None
        check(
            f"{app.profile['profile_id']}: install-hooks succeeds",
            run.returncode == 0,
            run.stderr.strip()[-200:],
        )

    found = our_commands(settings)
    script = str(shared / "duckterm-hook.sh")
    check(
        "one hook per event, all on the shared handler, no app path",
        bool(found)
        and all(len(v) == 1 and v[0][0] == script for v in found.values())
        and ".app/Contents" not in settings.read_text(),
        {e: [a[0] for a in v] for e, v in found.items()},
    )
    check(
        "install keeps the user's own settings",
        json.loads(settings.read_text()).get("model") == "qa-keep",
    )
    jq = shared / "jq"
    ran = (
        subprocess.run([str(jq), "-c", "."], input="1", capture_output=True, text=True)
        if jq.exists()
        else None
    )
    check(
        "shared assets present and the copied jq runs outside any bundle",
        all(
            (shared / n).is_file()
            for n in ("duckterm-hook.sh", "assets.json", "registrations.json")
        )
        and os.access(shared / "duckterm-hook.sh", os.X_OK)
        and ran is not None
        and ran.returncode == 0
        and ran.stdout.strip() == "1",
        ran and (ran.returncode, ran.stderr.strip()[-120:]),
    )
    rc, members = apps[-1].run_python(MEMBERS)
    check(
        "registered profiles are exactly the installing profiles",
        rc == 0 and set(json.loads(members)) == {k for k in profile_keys.values() if k},
        members,
    )

    # Remove one app outright; the shared handler must keep working for the rest.
    gone, user = apps[0], apps[1]
    gone.stop()
    shutil.rmtree(gone.app, ignore_errors=True)
    key = keys.get(user.profile["profile_id"])
    before = len(user.events(key or ""))
    done = root / "shared-hook-after-removal.json"
    if key:
        pane_run(
            user,
            key,
            f'echo \'{{"session_id":"pe","message":"shared"}}\' | \'{script}\' '
            f"Notification claude-code; echo '{{}}' > '{done}'",
            done,
        )
        time.sleep(1.5)
    check(
        f"after {gone.profile['profile_id']}'s app is deleted, the shared hook still "
        f"delivers {user.profile['profile_id']}'s events",
        bool(key) and len(user.events(key)) > before and Path(script).exists(),
    )

    # Uninstall is scoped to the caller's membership.
    for app in apps[1:]:
        rc_before, before_members = app.run_python(MEMBERS)
        run = subprocess.run(
            [str(app.cli), "uninstall-hooks", "--global", "--agent", "claude-code"],
            env=app.env,
            capture_output=True,
            text=True,
        )
        rc_after, after_members = app.run_python(MEMBERS)
        removed = (
            set(json.loads(before_members)) - set(json.loads(after_members))
            if rc_before == 0 and rc_after == 0
            else None
        )
        remaining = set(json.loads(after_members)) if rc_after == 0 else None
        check(
            f"{app.profile['profile_id']}: uninstall-hooks removes only its own membership",
            run.returncode == 0 and removed == {profile_keys[app.profile["profile_id"]]},
            {"removed": removed, "stderr": run.stderr.strip()[-160:]},
        )
        check(
            f"{app.profile['profile_id']}: the hook entry stays while other members remain",
            remaining is not None and (bool(our_commands(settings)) == bool(remaining)),
            {"remaining": remaining, "events": len(our_commands(settings))},
        )
    check(
        "uninstall keeps the user's own settings",
        json.loads(settings.read_text()).get("model") == "qa-keep",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apps", nargs="+", type=Path, help="app bundles to run side by side")
    parser.add_argument(
        "--release",
        action="store_true",
        help="real builds: require every profile manifest key in Info.plist",
    )
    args = parser.parse_args()
    if not args.release:
        print("NOTE  provisional run: --release not given, manifest keys not required\n")
    originals_before = {str(p.resolve()): bundle_state(p.resolve()) for p in args.apps}

    # Short private socket dir: macOS caps socket paths near 104 bytes.
    tmux_dir = Path(tempfile.mkdtemp(prefix="pe-", dir="/tmp"))
    # Resolved: macOS temp paths sit under the /var -> /private/var symlink, which
    # would hide the hook logic behind the symlinked-home case checked separately.
    root = Path(tempfile.mkdtemp(prefix="duckterm-pe-")).resolve()
    home = root / "home"
    home.mkdir()
    work = root / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.name=qa",
            "-c",
            "user.email=qa@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    base_env = {
        "HOME": str(home),
        "USER": os.environ.get("USER", "qa"),
        "TMPDIR": str(root),
        "TMUX_TMPDIR": str(tmux_dir),
        "TERM": "xterm-256color",
        "DUCKTERM_NO_BROWSER": "1",
        "DUCKTERM_RELEASE_CHECK": "off",
    }
    apps: list[App] = []
    try:
        for source in args.apps:
            app = App(source.resolve(), root, base_env)
            app.env["PATH"] = f"{app.cli.parent}:/usr/bin:/bin:/usr/sbin:/sbin"
            apps.append(app)
        App.tmux_binary = root / "tmux"
        shutil.copy2(apps[0].app / "Contents/Resources/tmux/bin/tmux", App.tmux_binary)

        # 1. Static contract: the bundle and its resolver agree.
        for app in apps:
            p = app.profile
            check(
                f"{app.app.name}: Info.plist identity matches the resolver contract",
                bool(p)
                and p.get("bundle_id") == app.bundle_id
                and p.get("app_name") == app.app_name,
                f"plist {app.bundle_id}/{app.app_name} vs {p}",
            )
            if args.release:
                wrong = {
                    key: (app.info.get(key), p.get(field))
                    for key, field in MANIFEST.items()
                    if app.info.get(key) != p.get(field)
                }
                check(f"{app.app.name}: every manifest key matches the contract", not wrong, wrong)
        for field in ("port", "home_name", "tmux_socket", "bundle_id"):
            values = [a.profile.get(field) for a in apps]
            check(f"profiles have distinct {field}", len(set(values)) == len(values), values)
        busy = [a.profile.get("port") for a in apps if port_busy(int(a.profile.get("port", 0)))]
        if busy:
            check("all profile ports are free on this machine (refusing to run)", False, busy)
            return 1

        # 1b. Ownership: never adopt a workspace the app didn't create.
        ownership_cases(apps[-1])

        # 2. Coexistence: every server up at once, each describing its own instance.
        for app in apps:
            check(f"{app.profile['profile_id']}: server starts", app.start(), app.log)
        for app in apps:
            if app.profile["profile_id"] == "full":
                continue  # the legacy Full workspace deliberately has no marker
            marker = app.home / "profile.json"
            want = {"format": 1, "profile_id": app.profile["profile_id"], "edition": app.edition}
            got = json.loads(marker.read_text()) if marker.exists() else None
            check(
                f"{app.profile['profile_id']}: new home carries its ownership marker",
                got == want,
                got,
            )
        for app in apps:
            status, edition = app.get("/edition")
            inst = edition.get("instance", {}) if isinstance(edition, dict) else {}
            check(
                f"{app.profile['profile_id']}: /edition reports its own edition and instance",
                status == 200
                and edition.get("id") == app.edition
                and inst.get("identity_version") == 1
                and inst.get("profile_id") == app.profile["profile_id"]
                and inst.get("port") == app.profile["port"]
                and inst.get("tmux_socket") == app.profile["tmux_socket"]
                and Path(str(inst.get("home", ""))).resolve() == app.home.resolve(),
                edition,
            )
        tokens = [a.token for a in apps]
        check("each instance has its own token", len(set(tokens)) == len(tokens) and all(tokens))

        # 3. Same-profile reuse is refused; the running server is unchanged.
        first = apps[0]
        pid = first.server.pid if first.server else None
        dup = subprocess.run(
            [str(first.cli), "serve", "--edition", first.edition],
            env=first.env,
            capture_output=True,
            text=True,
            timeout=40,
        )
        check(
            f"{first.profile['profile_id']}: a second server for the same profile is refused",
            dup.returncode != 0
            and first.server is not None
            and first.server.poll() is None
            and first.server.pid == pid
            and first.get("/sessions")[0] == 200,
            dup.stderr.strip()[-200:],
        )

        # 4. Sessions, pane identity and hook routing per instance.
        keys: dict[str, str] = {}
        for app in apps:
            key = launch(app, work, f"pe-{app.profile['profile_id']}", cwd=str(work))
            check(f"{app.profile['profile_id']}: test session launches", bool(key))
            if key:
                keys[app.profile["profile_id"]] = key
        hook_rel = SITE + "/hooks/duckterm-hook.sh"
        for app in apps:
            key = keys.get(app.profile["profile_id"])
            if not key:
                continue
            out = root / f"pane-{app.profile['profile_id']}.json"
            seen = pane_run(
                app,
                key,
                'printf \'{"home":"%s","url":"%s","profile":"%s","sock":"%s"}\' '
                '"${DUCKTERM_HOME-}" "${DUCKTERM_URL-}" "${DUCKTERM_DESKTOP_PROFILE-}" '
                f"\"${{DUCKTERM_TMUX_SOCKET-}}\" > '{out}'",
                out,
            )
            check(
                f"{app.profile['profile_id']}: pane env names its own home, URL, profile, socket",
                Path(seen.get("home", "/nonexistent")).resolve() == app.home.resolve()
                and seen.get("url") == app.base
                and seen.get("profile") == app.profile["profile_id"]
                and seen.get("sock") == app.profile["tmux_socket"],
                seen,
            )
            # The real hook, run with the pane's own environment.
            before = {
                a.profile["profile_id"]: len(a.events(keys.get(a.profile["profile_id"], "")))
                for a in apps
            }
            done = root / f"hook-{app.profile['profile_id']}.json"
            pane_run(
                app,
                key,
                f'echo \'{{"session_id":"pe","message":"routing"}}\' | '
                f"'{app.app / hook_rel}' Notification claude-code; echo '{{}}' > '{done}'",
                done,
            )
            time.sleep(1.5)
            after = {
                a.profile["profile_id"]: len(a.events(keys.get(a.profile["profile_id"], "")))
                for a in apps
            }
            own = after[app.profile["profile_id"]] > before[app.profile["profile_id"]]
            others = [p for p in after if p != app.profile["profile_id"] and after[p] != before[p]]
            check(
                f"{app.profile['profile_id']}: its pane's hook event reaches only its own server",
                own and not others,
                {"before": before, "after": after},
            )

        # 5. Worktree containment: a repo launch stays under its own home.
        for app in apps:
            status, body = app.post(
                "/sessions/launch",
                {
                    "command": "sh",
                    "in_terminal": False,
                    "test": True,
                    "name": "pe-wt",
                    "repo_path": str(work),
                },
            )
            key = body.get("session_key") if isinstance(body, dict) else None
            row = (
                next(
                    (
                        r
                        for r in (app.get("/sessions")[1] or {}).get("sessions", [])
                        if r.get("session_key") == key
                    ),
                    {},
                )
                if key
                else {}
            )
            wt = Path(str(row.get("worktree_path") or "/missing"))
            inside = str(wt.resolve()).startswith(str((app.home / "worktrees").resolve()) + "/")
            removed = False
            if key and inside:
                request(app.base, app.token, "DELETE", f"/sessions/{key}", {"force": True})
                removed = wait_for(lambda wt=wt: not wt.exists(), 15)
            check(
                f"{app.profile['profile_id']}: worktree stays in its home, removed with session",
                inside and removed,
                {"worktree": str(wt), "removed": removed},
            )

        # 6. Restart and quit isolation.
        target, rest = apps[-1], apps[:-1]
        pids = {a.profile["profile_id"]: a.server.pid for a in rest if a.server}
        panes = {a.profile["profile_id"]: a.tmux("ls").stdout for a in rest}
        target.stop()
        alive = all(a.get("/sessions")[0] == 200 for a in rest)
        check(f"stopping {target.profile['profile_id']} leaves the others serving", alive)
        restarted = target.start()
        key = keys.get(target.profile["profile_id"])
        rows = (target.get("/sessions")[1] or {}).get("sessions", []) if restarted else []
        readopted = any(
            r.get("session_key") == key and r.get("state") not in {"terminated", "stopped"}
            for r in rows
        )
        check(
            f"{target.profile['profile_id']}: restart re-adopts its own pane",
            restarted and readopted,
        )
        check(
            "the other instances' server PIDs and panes are unchanged",
            all(a.server and a.server.pid == pids[a.profile["profile_id"]] for a in rest)
            and all(a.tmux("ls").stdout == panes[a.profile["profile_id"]] for a in rest),
        )

        # 7. Shared hooks: one app-independent handler, membership per profile.
        symlinked_home_install(apps[-1], root)
        shared_hooks(apps, root, keys)

        # 8. Connector credentials are namespaced per profile (no Keychain access).
        results = [
            app.run_python(
                "from duckterm import connectors; print(connectors._keychain_service('github'))"
            )
            for app in apps
            if app.app.exists()
        ]
        services = [out for rc, out in results if rc == 0 and out]
        check(
            "connector Keychain services resolve and differ per profile",
            len(services) == len(results) and len(set(services)) == len(services),
            results,
        )
    finally:
        reaped = {a.profile.get("profile_id"): a.stop() for a in apps}
        for app in apps:
            app.tmux("kill-server")
        time.sleep(1)
        # Prove nothing is still running before deleting the evidence.
        listening = [
            a.profile.get("port") for a in apps if port_busy(int(a.profile.get("port", 0)))
        ]
        live_sockets = [a.profile.get("tmux_socket") for a in apps if a.tmux("ls").returncode == 0]
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(tmux_dir, ignore_errors=True)
        originals_after = {str(a.source): bundle_state(a.source) for a in apps}
    check("cleanup: every server child was reaped", all(reaped.values()), reaped)
    check("cleanup: no profile port is still listening", not listening, listening)
    check("cleanup: no profile tmux server is still running", not live_sockets, live_sockets)
    leftovers = [p for p in (root, tmux_dir) if p.exists()]
    check("cleanup: no temporary files remain", not leftovers, leftovers)
    check(
        "original bundles: signature and every file unchanged",
        originals_after == originals_before,
        [s for s in originals_after if originals_after[s] != originals_before.get(s)],
    )
    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
