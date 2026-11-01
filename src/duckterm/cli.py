"""Command-line entry point. Subcommands land in their respective Acts:
serve (Act 1), launch/fork (Acts 3/5), emit (Act 1+)."""

import argparse
import asyncio
import errno
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Coroutine, Sequence
from pathlib import Path
from typing import Any

from duckterm import __version__, editions
from duckterm.core import events
from duckterm.helpers import instance

DEFAULT_HOST = "127.0.0.1"


def _server_url() -> str:
    return instance.server_url()


def _installable_agents() -> list[str]:
    """Agents the registry can wire hooks for — the --agent choices."""
    from duckterm.harnesses import installable_agents

    return installable_agents()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="duckterm")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the orchestrator server")
    serve.add_argument("--host", default=os.environ.get("DUCKTERM_HOST", DEFAULT_HOST))
    serve.add_argument("--port", type=int, default=instance.port())
    serve.add_argument(
        "--edition", choices=list(editions.PRESETS), help="desktop features for this instance"
    )
    serve.add_argument(
        "--reload",
        action="store_true",
        help="dev: restart the server when a source file changes (watches src/duckterm)",
    )

    restart = sub.add_parser(
        "restart",
        help="stop the running server and start a fresh one (re-watches live agents)",
    )
    restart.add_argument("--host", default=os.environ.get("DUCKTERM_HOST", DEFAULT_HOST))
    restart.add_argument("--port", type=int, default=instance.port())
    restart.add_argument(
        "--edition", choices=list(editions.PRESETS), help="desktop features after restarting"
    )

    launch = sub.add_parser("launch", help="launch a supervised agent in a running server")
    launch.add_argument(
        "agent_command",
        metavar="command",
        help="agent invocation, e.g. 'claude' (quote the whole thing)",
    )
    launch.add_argument("--cwd", default=os.getcwd())
    launch.add_argument("--session-key", default=None)
    launch.add_argument("--prompt", default="")

    run = sub.add_parser(
        "run",
        help="run an agent in THIS terminal as a launched session "
        "(e.g. duckterm run --name login claude)",
    )
    # Duckterm's own options come BEFORE the agent; everything after the agent
    # name is passed through to it (REMAINDER keeps argparse from grabbing the
    # agent's own flags like --resume).
    run.add_argument("--name", default=None, help="a label for this session in the dashboard")
    run.add_argument("agent", help="the agent to run, e.g. claude / codex / copilot")
    run.add_argument("agent_args", nargs=argparse.REMAINDER, help="args passed to the agent")

    sub.add_parser("snapshot", help="bundle recently-active sessions to disk")
    backup = sub.add_parser(
        "backup", help="Archive the database, checkpoints, and agent transcripts"
    )
    backup.add_argument("--to", help="Local directory/archive.tar.gz or gs://BUCKET/prefix")
    backup.add_argument(
        "--sync", action="store_true", help="Sync changed files to GCS; retain a full local archive"
    )
    voice = sub.add_parser(
        "voice", help="install or remove Oracle's natural voice (Kokoro, local, Apple Silicon)"
    )
    voice.add_argument("action", choices=["status", "install", "remove"])
    sub.add_parser("dashboard", help="build (if needed) and open the dashboard in a browser")
    sub.add_parser("purge-test", help="delete all test/seed sessions and their data (test=1)")
    sub.add_parser("doctor", help="check deps, server, hooks, and trust; print what's missing")

    inst = sub.add_parser(
        "install-hooks", help="wire an agent so its sessions stream into Duckterm"
    )
    inst.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="install into the user-level config (every project) instead of this repo",
    )
    inst.add_argument(
        "--agent",
        choices=_installable_agents(),
        default="claude-code",
        help="which agent to wire (default: claude-code). codex: prefer --global "
        "(repo-local hooks are unreliable upstream)",
    )

    uninst = sub.add_parser("uninstall-hooks", help="remove Duckterm's hooks for an agent")
    uninst.add_argument("--global", dest="global_scope", action="store_true")
    uninst.add_argument("--agent", choices=_installable_agents(), default="claude-code")

    removal = sub.add_parser(
        "uninstall", help="preview uninstall cleanup without changing anything"
    )
    removal.add_argument(
        "--dry-run", action="store_true", help="inspect only (currently the default)"
    )
    removal.add_argument(
        "--project",
        action="append",
        type=Path,
        default=[],
        help="also inspect this project's hooks; repeat for other projects",
    )

    crun = sub.add_parser(
        "connector-run",
        help="exec a connector's MCP server with its credential resolved at launch "
        "(written into harness MCP configs by the Connectors panel — not run by hand)",
    )
    crun.add_argument("name", help="connector name (e.g. github)")
    broker = sub.add_parser(
        "connector-serve",
        aliases=["connector-service"],
        help="Serve configured connectors over mutual TLS",
    )
    broker.add_argument("--config", default=Path("/etc/duckterm-broker/config.json"), type=Path)
    attach = sub.add_parser(
        "connector-attach", help="Enroll this machine with a shared connector host"
    )
    attach.add_argument("--config", required=True, type=Path)
    admin = sub.add_parser(
        "connector-admin", help="Manage a hosted connector's Secret Manager version"
    )
    admin.add_argument("name")
    admin.add_argument("--config", default=Path("/etc/duckterm-broker/config.json"), type=Path)
    admin.add_argument("--version", type=int)
    admin.add_argument("--write-access", action="store_true")
    admin.add_argument("--disable", action="store_true")
    from duckterm import session_client

    session_client.add_parser(sub)
    session_client.add_memory_parser(sub)
    from duckterm.mobile.cli import add_parser as add_ducky_parser

    add_ducky_parser(sub)
    return parser


def _serve(host: str, port: int, reload: bool = False, *, recover: bool = False) -> int:
    import socket

    from duckterm.helpers import profile_state

    selected = editions.configured()
    instance.validate_desktop_context(selected.name)
    try:
        new_home = profile_state.validate_home()
    except ValueError as exc:
        print(f"duckterm: {exc}", file=sys.stderr)
        return 1
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("duckterm: server must bind a loopback host", file=sys.stderr)
        return 1
    if instance.configured_profile() and port != instance.port():
        print("duckterm: port conflicts with the desktop profile", file=sys.stderr)
        return 1
    os.environ["DUCKTERM_PORT"] = str(port)

    if reload:
        from duckterm.dev_reload import watch_and_reexec

        watch_and_reexec()  # background thread; re-execs this process on a .py change

    async def run(serve: Coroutine[Any, Any, None]) -> None:
        import signal

        from duckterm.stall_diagnostics import watch_stalls

        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        assert task is not None
        stopping = False

        def stop() -> None:
            nonlocal stopping
            # A repeated signal must not cancel the resource cleanup itself.
            if not stopping:
                stopping = True
                task.cancel()

        previous = signal.getsignal(signal.SIGTERM)
        loop.add_signal_handler(signal.SIGTERM, stop)
        try:
            with watch_stalls(instance.home() / "server-stalls.log"):
                await serve
        except asyncio.CancelledError:
            if not stopping:
                raise
        finally:
            loop.remove_signal_handler(signal.SIGTERM)
            signal.signal(signal.SIGTERM, previous)

    try:
        # Bind BEFORE opening SQLite, reconciling terminals, or changing hooks.
        # Holding this socket closes the preflight race with a competing launch.
        with socket.socket(socket.AF_INET6 if host == "::1" else socket.AF_INET) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
            listener.listen(100)
            listener.setblocking(False)
            from duckterm.server import Server, _acquire_home_lock, _release_home_lock

            if new_home:
                try:
                    instance.home().mkdir(mode=0o700)
                except FileExistsError:
                    new_home = profile_state.validate_home()
            lock = _acquire_home_lock()
            try:
                if new_home:
                    profile_state.remember_new_home()
                if recover:
                    from duckterm.persistence.history import HistoryStore

                    store = HistoryStore()
                    try:
                        cleared = store.clear_tombstones()
                    finally:
                        store.close()
                    if cleared:
                        print(
                            f"cleared {cleared} deletion(s) — live agents will re-appear",
                            file=sys.stderr,
                        )
                asyncio.run(
                    run(
                        Server(edition=selected).serve(
                            host,
                            port,
                            on_listening=_print_listening,
                            reserved_socket=listener,
                            home_lock=lock,
                        )
                    )
                )
            finally:
                _release_home_lock(lock)
    except KeyboardInterrupt:
        return 0
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        _print_port_in_use(host, port)
        return 1
    except ValueError as exc:
        print(f"duckterm: {exc}", file=sys.stderr)
        return 1
    return 0


def _listening_pids(port: int) -> set[int]:
    """PIDs currently bound to the local dashboard port."""
    import subprocess

    result = subprocess.run(
        ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise OSError("Cannot identify the server listening on the dashboard port")
    return {int(line) for line in result.stdout.splitlines() if line.isdecimal()}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _restart(host: str, port: int, *, edition: str | None = None) -> int:
    """Stop the old server completely before opening its SQLite database."""
    import signal
    import subprocess
    import time

    from duckterm.helpers import profile_state

    # Old terminals retain their original environment after an edition change.
    # Validate and select the last successful startup before stopping anything.
    selected = editions.PRESETS[edition] if edition else editions.configured(prefer_saved=True)
    os.environ["DUCKTERM_EDITION"] = selected.name
    instance.validate_desktop_context(selected.name)
    try:
        profile_state.validate_home()
    except ValueError as exc:
        print(f"duckterm: {exc}", file=sys.stderr)
        return 1
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("duckterm: restart requires a loopback host", file=sys.stderr)
        return 1
    if instance.configured_profile() and port != instance.port():
        print("duckterm: port conflicts with the desktop profile", file=sys.stderr)
        return 1
    if _duckterm_responds(host, port):
        if not _restart_identity_matches(host, port):
            print(
                f"Cannot verify this workspace owns the server on :{port}; left it running",
                file=sys.stderr,
            )
            return 1
        print(f"stopping the server on :{port}…", file=sys.stderr)
        try:
            old_pids = _listening_pids(port)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"cannot identify the server on :{port}: {exc}", file=sys.stderr)
            return 1
        if not old_pids:
            print(f"server on :{port} changed; retry restart", file=sys.stderr)
            return 1
        try:
            owner_pid = int((instance.home() / "server.pid").read_text())
        except (OSError, ValueError):
            owner_pid = 0
        if old_pids != {owner_pid}:
            print(
                f"Cannot verify the listening process owns {instance.home()}; left it running",
                file=sys.stderr,
            )
            return 1
        for pid in old_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except PermissionError:
                print(f"cannot stop the server on :{port}", file=sys.stderr)
                return 1
            except ProcessLookupError:
                pass  # It exited between discovery and SIGTERM.
        for _ in range(50):  # Up to 10 seconds for shutdown and DB close.
            if not any(_pid_alive(pid) for pid in old_pids):
                break
            time.sleep(0.2)
        else:
            print(f"server on :{port} did not exit; stop it before retrying", file=sys.stderr)
            return 1
        try:
            replacement = _listening_pids(port)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"cannot verify the port on :{port}: {exc}", file=sys.stderr)
            return 1
        if replacement:
            print(f"another server took :{port}; stop it before retrying", file=sys.stderr)
            return 1
    else:
        print(f"no server running on :{port}; starting one", file=sys.stderr)
    # Recovery waits until serve owns BOTH the port and the home. A non-Duckterm
    # listener must not cause tombstone/DB writes before bind reports failure.
    return _serve(host, port, recover=True)


def _print_listening(host: str, port: int) -> None:
    url = f"http://{host}:{port}"
    print(f"duckterm serving on {url}", file=sys.stderr)
    # Open the dashboard automatically so `duckterm serve` is one command with no
    # follow-up copy-paste. Only when a UI is actually bundled (a dev checkout
    # without a build would just open a "server running" text page); and never
    # when the user asked us not to (DUCKTERM_NO_BROWSER, set by tests/headless).
    from duckterm.transport.httpio import dashboard_dir

    if dashboard_dir() is not None and not os.environ.get("DUCKTERM_NO_BROWSER"):
        import webbrowser

        webbrowser.open(_login_url(url))


def _print_port_in_use(host: str, port: int) -> None:
    print(f"duckterm: Port {port} is already in use.", file=sys.stderr)
    if _duckterm_responds(host, port):
        print(
            f"\nAnother Duckterm is already running:\n  open http://{host}:{port}",
            file=sys.stderr,
        )
    print(
        "\nInspect the existing process before stopping it. For a separate custom workspace, "
        "use a distinct DUCKTERM_INSTANCE and an unused port. "
        "Desktop editions keep their own ports.",
        file=sys.stderr,
    )


def _restart_identity_matches(host: str, port: int) -> bool:
    """Read-only authenticated proof; never create a token during discovery."""
    from duckterm.helpers import security

    try:
        token = (instance.home() / "token").read_text().strip()
        if not token:
            return False
        request = urllib.request.Request(
            f"http://{host}:{port}/edition", headers={security.TOKEN_HEADER: token}
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)
        identity = payload.get("instance")
        if identity is None:
            # Published legacy servers have no identity document. Accept only
            # the default workspace; the caller separately verifies its pidfile.
            profile = instance.configured_profile()
            return (
                payload.get("id") in editions.PRESETS
                and (profile is None or payload.get("id") == profile["edition"])
                and instance.instance_id() is None
                and instance.home().resolve() == (Path.home() / ".duckterm").resolve()
                and port == 4300
            )
        expected = instance.description()
        profile = instance.configured_profile()
        if profile is not None and (
            payload.get("id") != profile["edition"]
            or not isinstance(identity, dict)
            or identity.get("profile_id")
            not in ({"full", None} if profile["profile_id"] == "full" else {profile["profile_id"]})
        ):
            return False
        return (
            isinstance(identity, dict)
            and all(
                identity.get(key) == expected[key]
                for key in ("identity_version", "instance_id", "home", "tmux_socket")
            )
            and identity.get("port") == port
        )
    except (OSError, ValueError, AttributeError):
        return False


def _login_url(url: str) -> str:
    """The dashboard needs the owner token; /login trades it for a cookie."""
    from duckterm.helpers import security

    return f"{url}/login?token={urllib.parse.quote(security.load_or_create_token())}"


def _duckterm_responds(host: str, port: int) -> bool:
    """True if whatever owns the port answers with Duckterm's self-probe
    header, so we can tell 'already running' from 'foreign process'."""
    try:
        resp = urllib.request.urlopen(f"http://{host}:{port}/", timeout=1)
    except urllib.error.HTTPError as locked:  # the sign-in page answers 401
        resp = locked
    except OSError:
        return False
    return bool(resp.headers.get("X-Duckterm") == "1")


def _auth_headers(content_type: bool = True) -> dict[str, str]:
    from duckterm.helpers import security

    headers = {security.TOKEN_HEADER: security.load_or_create_token()}
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def _launch(command: str, cwd: str, session_key: str | None, prompt: str) -> int:
    payload = {"command": command, "cwd": cwd, "session_key": session_key, "prompt": prompt}
    req = urllib.request.Request(
        f"{_server_url()}/sessions/launch",
        data=json.dumps(payload).encode(),
        headers=_auth_headers(),
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
    except OSError as e:
        print(f"could not reach server at {_server_url()}: {e}", file=sys.stderr)
        return 1
    print(json.load(resp)["session_key"])
    return 0


def _run(agent: str, agent_args: list[str], name: str | None = None) -> int:
    """Run an agent in the CURRENT terminal as a LAUNCHED Duckterm session.
    Registers the session (so it appears immediately, owned by Duckterm), then
    execs the agent wrapped with a heartbeat loop — the agent owns the TTY
    natively (signals, exit code, prompts) and the row stays live while it runs.
    A session is 'launched' purely from the SessionStart flag + heartbeat, not
    from owning a new terminal — so no extra window is opened."""
    import shlex

    from duckterm.agents.terminal import with_heartbeat
    from duckterm.harnesses import infer_runtime
    from duckterm.helpers import security

    if not shutil.which(agent):
        print(f"'{agent}' not found on PATH", file=sys.stderr)
        return 127

    runtime = infer_runtime(agent)
    if runtime != "generic" and not _hook_installed(runtime):
        print(
            f"⚠ {runtime} hooks aren't installed, so this session won't appear in "
            f"Duckterm.\n  Fix: duckterm install-hooks --agent {runtime} --global"
            f"  (then: duckterm doctor)",
            file=sys.stderr,
        )

    cwd = os.getcwd()
    key = security.new_session_key("run")
    # Register a launched session up front so the row shows immediately. If the
    # server is down, fall through to a plain exec (the agent still runs).
    if _duckterm_responds(DEFAULT_HOST, instance.port()):
        _register_run_session(key, agent, runtime, cwd, name)
    else:
        print(
            "⚠ no Duckterm server on this instance's port — running anyway, "
            "but it won't appear.\n"
            "  Start one in another terminal: duckterm serve",
            file=sys.stderr,
        )

    agent_cmd = " ".join(shlex.quote(a) for a in [agent, *agent_args])
    # Wrap with the heartbeat loop (current tab's tty), under the session key the
    # agent's hooks will also report against, then exec a shell so it owns the TTY.
    wrapped = with_heartbeat(agent_cmd, instance.heartbeat_url(), key)
    from duckterm.helpers import session_credentials

    os.environ.update(session_credentials.launch_env(key))
    os.execvp("sh", ["sh", "-c", wrapped])  # replaces this process; doesn't return


def _register_run_session(key: str, agent: str, runtime: str, cwd: str, name: str | None) -> None:
    """POST a launched SessionStart (and the optional name) so the dashboard row
    appears before the agent starts. Best-effort: a failure just means the row
    shows up a beat later from the agent's own hooks."""
    start = {
        "event_type": events.SESSION_START,
        "session_key": key,
        "runtime": runtime,
        "cwd": cwd,
        "source_app": name or os.path.basename(cwd.rstrip("/")) or agent,
        "launched": True,
    }
    try:
        _post_json("/events", start)
        if name:
            _patch_json(f"/sessions/{key}", {"name": name})
    except OSError:
        pass


def _post_json(path: str, payload: dict[str, object]) -> None:
    req = urllib.request.Request(
        f"{_server_url()}{path}",
        data=json.dumps(payload).encode(),
        headers=_auth_headers(),
        method="POST",
    )
    urllib.request.urlopen(req, timeout=3).read()


def _patch_json(path: str, payload: dict[str, object]) -> None:
    req = urllib.request.Request(
        f"{_server_url()}{path}",
        data=json.dumps(payload).encode(),
        headers=_auth_headers(),
        method="PATCH",
    )
    urllib.request.urlopen(req, timeout=3).read()


def _hook_installed(runtime: str) -> bool:
    """True if Duckterm's hook is wired into the agent's global settings."""
    from duckterm.agents.hooks_install import hook_script_path, settings_path

    try:
        path = settings_path(global_scope=True, project_dir=Path.cwd(), agent=runtime)
    except (KeyError, ValueError):
        return False  # unknown/generic runtime has no hook to install
    return path.exists() and str(hook_script_path()) in path.read_text()


def _snapshot() -> int:
    req = urllib.request.Request(
        f"{_server_url()}/snapshots", data=b"", headers=_auth_headers(False), method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except OSError as e:
        print(f"could not reach server at {_server_url()}: {e}", file=sys.stderr)
        return 1
    print(json.load(resp)["id"])
    return 0


def _install_hooks(global_scope: bool, agent: str) -> int:
    from duckterm.agents.hooks_install import install

    if agent == "codex" and not global_scope:
        print(
            "note: codex repo-local hooks are unreliable upstream "
            "(openai/codex#17532). Prefer: duckterm install-hooks --codex --global",
            file=sys.stderr,
        )
    path = install(global_scope=global_scope, project_dir=Path.cwd(), agent=agent)
    scope = "every project" if global_scope else "this repo"
    print(f"installed Duckterm hooks for {agent} ({scope}): {path}")
    print(f"Now run `duckterm serve`, then start {agent} — sessions appear automatically.")
    if agent == "codex":
        # Codex won't run a hook until you trust it (recorded against the
        # script's hash; revoked if the script changes). There's no
        # programmatic trust — it's an interactive review.
        print(
            "\nOne extra step for codex: hooks must be TRUSTED before they run.\n"
            "  Start codex, run /hooks, and trust the Duckterm hook.\n"
            "  (You'll need to re-trust if Duckterm updates the hook script.)",
            file=sys.stderr,
        )
    print("\nVerify it's wired up: duckterm doctor")
    return 0


def _uninstall_hooks(global_scope: bool, agent: str) -> int:
    from duckterm.agents.hooks_install import uninstall

    path = uninstall(global_scope=global_scope, project_dir=Path.cwd(), agent=agent)
    print(f"removed Duckterm {agent} hooks from {path}")
    return 0


def _dashboard() -> int:
    import subprocess
    import webbrowser

    from duckterm.transport.httpio import dashboard_dir

    if dashboard_dir() is None:
        web = Path(__file__).resolve().parents[2] / "web"
        if not web.exists():
            print("dashboard source (web/) not found", file=sys.stderr)
            return 1
        print("building the dashboard (web/)…", file=sys.stderr)
        build = subprocess.run(["npm", "run", "build"], cwd=web)
        if build.returncode != 0:
            print("dashboard build failed", file=sys.stderr)
            return 1
    url = _server_url()
    try:
        urllib.request.urlopen(f"{url}/", timeout=2)
    except urllib.error.HTTPError:
        pass  # the sign-in page answers 401; the server is up
    except OSError:
        print(f"server not reachable at {url} — start it with `duckterm serve`", file=sys.stderr)
        return 1
    print(f"opening {url}")
    webbrowser.open(_login_url(url))
    return 0


def _doctor() -> int:
    from duckterm import doctor

    results = doctor.run(f"{_server_url()}/", _installable_agents())
    glyph = {"ok": "\033[32m✓\033[0m", "warn": "\033[33m!\033[0m", "fail": "\033[31m✗\033[0m"}
    for r in results:
        print(f"{glyph[r.status]} {r.title}")
        if r.detail and r.status != "ok":
            print(f"    → {r.detail}")
    fails = sum(1 for r in results if r.status == "fail")
    warns = sum(1 for r in results if r.status == "warn")
    print()
    if fails:
        print(f"\033[31m{fails} problem(s) to fix.\033[0m", file=sys.stderr)
    elif warns:
        print(f"\033[33m{warns} thing(s) to check; nothing fatal.\033[0m")
    else:
        print("\033[32mAll checks passed.\033[0m")
    return 1 if fails else 0


def main(argv: Sequence[str] | None = None) -> int:
    from duckterm.helpers.desktop_cli import apply_app_profile

    try:
        apply_app_profile()
    except (OSError, ValueError) as exc:
        print(f"duckterm: {exc}", file=sys.stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.command is None:
        parser.print_help()
        return 0
    if selected := getattr(args, "edition", None):
        os.environ["DUCKTERM_EDITION"] = selected
    try:
        profile = instance.configured_profile()
    except ValueError as exc:
        print(f"duckterm: {exc}", file=sys.stderr)
        return 1
    if profile is not None:
        from duckterm.helpers import profile_state

        try:
            instance.validate_desktop_context(editions.configured().name)
            new_home = profile_state.validate_home()
            if new_home and args.command not in {
                "serve",
                "restart",
                "doctor",
                "install-hooks",
                "uninstall-hooks",
                "uninstall",
            }:
                raise ValueError("Open this edition's app before using workspace commands")
        except ValueError as exc:
            print(f"duckterm: {exc}", file=sys.stderr)
            return 1
    optional = {
        "voice": "oracle",
        "snapshot": "checkpoints",
        "connector-run": "connectors",
        "connector-serve": "connectors",
        "connector-service": "connectors",
        "connector-attach": "connectors",
        "connector-admin": "connectors",
    }
    if args.command in optional or args.command in {"serve", "restart"}:
        try:
            edition = editions.configured(
                prefer_saved=args.command in optional
                or (args.command == "restart" and not args.edition)
            )
            feature = optional.get(args.command)
            if feature and not edition.includes(feature):
                raise ValueError(f"{feature} is unavailable in Duckterm {edition.name.title()}")
            if feature:
                # Standalone handlers and children must use the same selection
                # even when invoked from a terminal launched in an older edition.
                os.environ["DUCKTERM_EDITION"] = edition.name
        except ValueError as exc:
            print(f"duckterm: {exc}", file=sys.stderr)
            return 1
    if args.command in {"session", "memory"}:
        from duckterm import session_client

        return session_client.main(args)
    if args.command == "ducky":
        from duckterm.mobile.cli import run as run_ducky

        return run_ducky(args)
    if args.command == "serve":
        return _serve(args.host, args.port, reload=args.reload)
    if args.command == "restart":
        return _restart(args.host, args.port, edition=args.edition)
    if args.command == "launch":
        return _launch(args.agent_command, args.cwd, args.session_key, args.prompt)
    if args.command == "run":
        return _run(args.agent, args.agent_args, args.name)
    if args.command == "backup":
        import sqlite3

        from duckterm.persistence import backup, backup_sync

        try:
            if args.sync:
                backup_sync.validate_mode(args.to, "sync")
                print(backup_sync.create(args.to).result)
            else:
                print(backup.create(args.to))
            return 0
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(f"duckterm: {exc}", file=sys.stderr)
            return 1
    if args.command == "voice":
        return _voice(args.action)
    if args.command == "snapshot":
        return _snapshot()
    if args.command == "dashboard":
        return _dashboard()
    if args.command == "install-hooks":
        return _install_hooks(args.global_scope, args.agent)
    if args.command == "uninstall":
        from duckterm.uninstall_plan import inventory

        report = inventory(instance.home(), args.project)
        print(json.dumps(report, indent=2))
        return 1 if any(h["state"] == "blocked" for h in report["hooks"]) else 0
    if args.command == "uninstall-hooks":
        return _uninstall_hooks(args.global_scope, args.agent)
    if args.command == "doctor":
        return _doctor()
    if args.command == "purge-test":
        return _purge_test()
    if args.command in (
        "connector-serve",
        "connector-service",
        "connector-attach",
        "connector-admin",
    ):
        from duckterm import connector_broker, connector_client

        try:
            if args.command in ("connector-serve", "connector-service"):
                asyncio.run(connector_broker.serve(args.config))
            elif args.command == "connector-attach":
                connector_client.attach(args.config)
                print("Shared connectors registered for Claude Code and Codex; start new sessions")
            else:
                connector_broker.admin(
                    args.config, args.name, args.version, args.write_access, args.disable
                )
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"duckterm: {exc}", file=sys.stderr)
            return 1
    if args.command == "connector-run":
        from duckterm import connectors

        connectors.run(args.name)  # execs the server; returns only on failure
        return 1
    print(f"command '{args.command}' is not implemented yet", file=sys.stderr)
    return 1


def _purge_test() -> int:
    from duckterm.persistence.history import HistoryStore

    purged = HistoryStore().purge_test_sessions()
    print(f"purged {len(purged)} test session(s) and all their data")
    return 0


def _voice(action: str) -> int:
    """Oracle's optional local voice. A running server picks up an install on
    its next status check; stop the worker there before removing."""
    from duckterm.helpers import paths
    from duckterm.voice.service import SIZE_NOTE, LocalVoice, VoiceError

    voice = LocalVoice(paths.home() / "voice")
    if action == "status":
        print(json.dumps(voice.status(), indent=2))
        return 0
    if action == "remove":
        voice.remove()
        print("Removed Oracle's natural voice.")
        return 0
    print(f"Installing Oracle's natural voice ({SIZE_NOTE}, into {voice.root}).")
    last = [""]

    def report(step: str, done: float) -> None:
        if step != last[0]:
            print(f"  {done:4.0%}  {step}")
            last[0] = step

    try:
        voice.install(report)
    except VoiceError as exc:
        print(f"duckterm: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
