"""Opt-in upgrade rehearsal; uses only a temporary home and unique tmux socket.

Run with the checkout's Python and --old-python pointing to an installed release.
No real model, global installation, production database, or production server is used.
"""

import argparse
import json
import os
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

AGENT = """import json, os, subprocess, sys
from pathlib import Path
key = os.environ["DUCKTERM_SESSION_KEY"]
root = Path(os.environ["DUCKTERM_HOME"])
memory = []
(root / (key + ".pid")).write_text(str(os.getpid()))
for line in sys.stdin:
    request = json.loads(line)
    memory.append(request["id"])
    result = {"pid": os.getpid(), "memory": memory}
    if "args" in request:
        env = dict(os.environ, PYTHONPATH=request["source"])
        proc = subprocess.run(
            [request["python"], "-c", "from duckterm.cli import main; main()",
             "session", *request["args"]], env=env, input=request.get("stdin", ""),
            text=True, capture_output=True, timeout=20)
        result.update(code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    destination = root / (request["id"] + ".json")
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(result))
    temporary.replace(destination)
"""


def wait_for(check, description):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {description}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-python", required=True, type=Path)
    args = parser.parse_args()
    assert shutil.which("tmux"), "tmux is required"
    source = Path(__file__).resolve().parents[1] / "src"
    namespace = "duckterm-rehearsal-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="duckterm-upgrade-") as directory:
        root = Path(directory)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("DUCKTERM_")}
        env.pop("PYTHONPATH", None)
        env.update(
            DUCKTERM_HOME=str(root),
            DUCKTERM_TMUX_SOCKET=namespace,
            DUCKTERM_PORT=str(port),
            DUCKTERM_URL=url,
            DUCKTERM_SUMMARIZER="off",
            DUCKTERM_NO_TERMINAL="1",
        )
        agent = root / "agent.py"
        agent.write_text(AGENT)
        process = None
        logs = []

        def request(method, path, body=None):
            headers = {"X-Duckterm-Token": (root / "token").read_text().strip()}
            if body is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                url + path,
                method=method,
                headers=headers,
                data=json.dumps(body).encode() if body is not None else None,
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.load(response)

        def start(python, upgraded):
            nonlocal process
            child_env = dict(env)
            if upgraded:
                child_env["PYTHONPATH"] = str(source)
            ready = root / "ready"
            ready.unlink(missing_ok=True)
            code = (
                "import asyncio; from pathlib import Path; from duckterm.server import Server; "
                f"asyncio.run(Server().serve('127.0.0.1', {port}, "
                f"on_listening=lambda *_: Path({str(ready)!r}).write_text('ready')))"
            )
            log = root / f"server-{len(logs)}.log"
            logs.append(log)
            with log.open("w") as output:
                process = subprocess.Popen(
                    [str(python), "-u", "-c", code],
                    cwd=root,
                    env=child_env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )

            def listening():
                assert process.poll() is None, log.read_text()
                return ready.exists()

            wait_for(listening, "isolated server listening")

        def stop():
            nonlocal process
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process = None

        def launch(key, folder):
            request(
                "POST",
                "/sessions/launch",
                {
                    "session_key": key,
                    "command": shlex.join([sys.executable, str(agent)]),
                    "cwd": str(root),
                    "in_terminal": False,
                },
            )
            request("PATCH", "/sessions/" + key, {"group": folder, "name": key})
            wait_for(lambda: (root / (key + ".pid")).exists(), "agent starts")

        def command(key, *cli_args, stdin=""):
            ident = uuid.uuid4().hex
            payload = {"id": ident}
            if cli_args:
                payload.update(
                    args=list(cli_args), source=str(source), python=sys.executable, stdin=stdin
                )
            request("POST", f"/sessions/{key}/input", {"text": json.dumps(payload) + "\n"})
            path = root / (ident + ".json")
            wait_for(path.exists, f"{key} responds to {cli_args}")
            result = json.loads(path.read_text())
            assert result["pid"] == int((root / (key + ".pid")).read_text())
            if cli_args:
                assert result["code"] == 0, result
                return json.loads(result["stdout"])
            return result

        def schema():
            with sqlite3.connect(f"file:{root / 'db.sqlite'}?mode=ro", uri=True) as db:
                return db.execute("PRAGMA user_version").fetchone()[0]

        def approval():
            item = request(
                "POST",
                "/approvals",
                {
                    "session_key": "alpha",
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo disposable-rehearsal"},
                },
            )
            return item["id"]

        try:
            start(args.old_python, False)
            launch("alpha", "project/backend")
            launch("beta", "project/frontend")
            before = {key: command(key) for key in ("alpha", "beta")}
            old_schema = schema()
            with (
                sqlite3.connect(f"file:{root / 'db.sqlite'}?mode=ro", uri=True) as db,
                sqlite3.connect(root / "backup.sqlite") as backup,
            ):
                db.backup(backup)
            pending = approval()
            stop()
            for result in before.values():
                os.kill(result["pid"], 0)
            start(sys.executable, True)
            assert schema() == 3
            for key, previous in before.items():
                current = command(key)
                assert current["pid"] == previous["pid"]
                assert current["memory"][: len(previous["memory"])] == previous["memory"]
                assert command(key, "self")["folder"].startswith("project/")
            assert request("GET", f"/approvals/{pending}/decision")["status"] == "gone"
            fresh = approval()
            request("POST", f"/approvals/{fresh}/decide", {"decision": "approve"})
            assert request("GET", f"/approvals/{fresh}/decision")["status"] == "approve"
            print(
                f"PASS: schema {old_schema} → 3; both PIDs and in-memory histories survived",
                flush=True,
            )
            print(
                "CONFIRMED: in-flight approval is lost on restart; fresh approvals work", flush=True
            )
            peers = command("alpha", "discover")["sessions"]
            assert [row["session_id"] for row in peers] == ["beta"]
            question = command("alpha", "ask", "beta", "What is the contract?")
            ident = question["id"]
            assert command("beta", "inbox")["messages"][0]["id"] == ident
            command("beta", "accept", ident)
            answer = "Complete reply with Unicode 🦆\n" * 1000
            answer_file = root / "answer.txt"
            answer_file.write_text(answer)
            command("beta", "reply", ident, "--file", str(answer_file))
            assert command("alpha", "get", ident)["answer"] == answer
            command("beta", "publish", "--activity", "Contract validated")
            assert command("beta", "self")["activity"] == "Contract validated"
            request("PATCH", "/folders/project", {"name": "renamed"})
            assert command("alpha", "self")["folder"] == "renamed/backend"
            assert command("alpha", "get", ident)["answer"] == answer
            request("PATCH", "/sessions/beta", {"group": "unrelated"})
            assert command("alpha", "discover")["sessions"] == []
            launch("gamma", "renamed/new")
            assert command("gamma", "self")["folder"] == "renamed/new"
            assert command("alpha", "discover")["sessions"][0]["session_id"] == "gamma"
            print(
                "PASS: enrollment, discovery, full inbox reply, card updates, "
                "folder boundaries, new launch",
                flush=True,
            )
            credentials = {
                path.name: path.read_bytes()
                for path in (root / "session-credentials").glob("*.json")
            }
            stop()
            start(sys.executable, True)
            assert credentials == {
                path.name: path.read_bytes()
                for path in (root / "session-credentials").glob("*.json")
            }
            for key in ("alpha", "beta", "gamma"):
                command(key, "self")
            assert command("beta", "inbox")["messages"] == []  # old root is inaccessible
            request("PATCH", "/sessions/beta", {"group": "renamed/frontend"})
            assert command("alpha", "get", ident)["answer"] == answer
            print(
                "PASS: repeated server restart preserves credentials and running agents", flush=True
            )
        except BaseException:
            for log in logs:
                print(log.read_text(), file=sys.stderr)
            raise
        finally:
            stop()
            subprocess.run(["tmux", "-L", namespace, "kill-server"], capture_output=True)
    print("Cleaned up disposable server, agents, database, and credentials.")


if __name__ == "__main__":
    main()
