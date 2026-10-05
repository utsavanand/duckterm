#!/usr/bin/env python3
"""Run browser checks with a private home, server port, state file and socket."""

import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    def interrupted(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    env = dict(os.environ)
    # Preserve the installed browser cache before replacing HOME for fixtures.
    if "PLAYWRIGHT_BROWSERS_PATH" not in env:
        cache = (
            Path.home() / "Library/Caches"
            if sys.platform == "darwin"
            else Path(env.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        )
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "ms-playwright")
    with tempfile.TemporaryDirectory(prefix="duckterm-browser-") as directory:
        root = Path(directory).resolve()
        home = root / "home"
        home.mkdir()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        # Closing the reservation leaves a small bind race; owned-server readiness
        # fails closed if another process wins, never reusing that server.
        namespace = "rd-e2e-" + uuid.uuid4().hex
        env.update(
            HOME=str(home),
            DUCKTERM_HOME=str(root / "state"),
            DUCKTERM_TMUX_SOCKET=namespace,
            DUCKTERM_NO_TERMINAL="1",
            DUCKTERM_NO_BROWSER="1",
            DUCKTERM_SUMMARIZER="off",
            RD_TEST_RUN_ROOT=str(root),
            RD_TEST_RUN_ID=uuid.uuid4().hex,
            RD_TEST_PORT=str(port),
            RD_TEST_STATE_FILE=str(root / "state.json"),
            RD_TEST_TMUX_SOCKET=namespace,
            DUCKTERM_SUPPORT_EMAIL="qa@example.test",
            DUCKTERM_NO_KEYCHAIN="1",
            PYTHONPATH=str(ROOT / "src"),
        )
        # Inherited provider roots can point outside HOME. The server assigns
        # its own fixture roots; workers must not inherit the user's overrides.
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("CODEX_HOME", None)
        print(f"Browser test home: {root}; port: {port}", flush=True)
        proc = subprocess.Popen(
            [str(ROOT / "web/node_modules/.bin/playwright"), "test", *sys.argv[1:]],
            cwd=ROOT / "web",
            env=env,
            start_new_session=True,
        )
        try:
            return proc.wait()
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            # The process group belongs to this invocation, including its HTTP
            # server if Playwright failed before normal teardown could run.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            # Playwright can exit before its server. Reap surviving members of
            # this invocation's group, including children ignoring SIGTERM.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    os.killpg(proc.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(FileNotFoundError):
                subprocess.run(["tmux", "-L", namespace, "kill-server"], capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
