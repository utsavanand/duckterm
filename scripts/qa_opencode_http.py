"""HTTP acceptance helper for the isolated, pinned OpenCode provider fixture."""

import contextlib
import json
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error


def verify(root, binary, env, namespace, native):
    home = root / "http-duckterm"
    home.mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    child_env = {k: v for k, v in env.items() if not k.startswith("DUCKTERM_")}
    child_env.update(
        PATH=str(binary.parent) + ":" + env["PATH"],
        PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[1] / "src"),
        DUCKTERM_HOME=str(home),
        DUCKTERM_TMUX_SOCKET=namespace,
        DUCKTERM_NO_TERMINAL="1",
        DUCKTERM_NO_BROWSER="1",
        DUCKTERM_SUMMARIZER="off",
        DUCKTERM_RELEASE_CHECK="off",
        CLAUDE_CONFIG_DIR=str(root / "http-claude"),
        CODEX_HOME=str(root / "http-codex"),
    )
    # The owning process installs its own observer, never the fixture's plugin.
    child_env.pop("OPENCODE_TUI_CONFIG", None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    keys = ["http-opencode-fixture", "http-sibling-fixture"]

    def request(method, path, body=None):
        headers = {"Content-Type": "application/json"}
        token = home / "token"
        if token.exists():
            headers["X-Duckterm-Token"] = token.read_text().strip()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with opener.open(req, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Fixture {method} {path}: {exc.code} {exc.read(4096)!r}") from exc

    with (root / "http-dashboard.log").open("w") as output:
        child = subprocess.Popen(
            [sys.executable, "-m", "duckterm.cli", "serve", "--port", str(port)],
            cwd=root / "project",
            env=child_env,
            stdout=output,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    options = request("GET", "/runtimes/opencode/launch-options")
                    break
                except OSError:
                    if child.poll() is not None:
                        raise RuntimeError("Isolated dashboard exited before readiness")
                    time.sleep(0.1)
            else:
                raise RuntimeError("Isolated dashboard did not become ready")
            assert options["launch"]["available"], options["launch"]
            for key, command in (
                (keys[1], "/bin/cat"),
                (keys[0], "opencode --model fixture/alpha"),
            ):
                result = request(
                    "POST",
                    "/sessions/launch",
                    dict(
                        session_key=key,
                        command=command,
                        cwd=str(root / "project"),
                        in_terminal=False,
                        test=True,
                        prompt="Return a short fixture response." if key == keys[0] else "",
                    ),
                )
                assert result["session_key"] == key and not result["opened_in_terminal"]
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                messages = request("GET", "/sessions/" + keys[0] + "/messages")
                if any(
                    m["role"] == "assistant" and m.get("complete") for m in messages["messages"]
                ):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("HTTP-launched native reply was not visible in Messages")
            assert messages["transcript"]["status"] == "native_current"
            rows = request("GET", "/sessions")["sessions"]
            assert {row["session_key"] for row in rows} >= set(keys)
            assert len([m for m in messages["messages"] if m["role"] == "user"]) == 1
            return {"passed": True, "same_dashboard": True, "native_messages": True}
        finally:
            # Retain the exact native identity for the outer fixture's deletion.
            import sqlite3

            with contextlib.suppress(sqlite3.Error, FileNotFoundError):
                with sqlite3.connect(home / "db.sqlite") as conn:
                    row = conn.execute(
                        "SELECT restart_json FROM sessions WHERE session_key=?", (keys[0],)
                    ).fetchone()
                    control = json.loads(row[0] or "{}") if row else {}
                    identity = control.get("opencode_launch", {}).get("session_id")
                    if identity and identity not in native:
                        native.append(identity)
            for key in keys:
                with contextlib.suppress(OSError, RuntimeError):
                    request("POST", "/sessions/" + key + "/stop", {})
                with contextlib.suppress(OSError, RuntimeError):
                    request("DELETE", "/sessions/" + key)
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            subprocess.run(["tmux", "-L", namespace, "kill-server"], capture_output=True)
