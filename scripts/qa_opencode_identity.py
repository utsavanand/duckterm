#!/usr/bin/env python3
"""Pinned macOS native identity probe; no provider calls or production state.

Usage: python3 scripts/qa_opencode_identity.py /absolute/path/to/pinned/opencode
Creates two native synthetic sessions in a disposable home and removes them.
"""
import base64
import hashlib
import http.server
import json
import pathlib
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

root = pathlib.Path(tempfile.mkdtemp(prefix="duckterm-opencode-probe-")).resolve()
binary = pathlib.Path(sys.argv[1]).resolve()
assert (
    hashlib.sha256(binary.read_bytes()).hexdigest()
    == "7b63b34fafabded7d9231f6a9032755d0cdeaf8b9d2b70df8e25535471469eea"
), "Unexpected native executable hash"
for name in ("home", "data", "config", "cache", "state", "tmp", "project"):
    (root / name).mkdir()
env = {
    "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    "HOME": str(root / "home"),
    "XDG_DATA_HOME": str(root / "data"),
    "XDG_CONFIG_HOME": str(root / "config"),
    "XDG_CACHE_HOME": str(root / "cache"),
    "XDG_STATE_HOME": str(root / "state"),
    "TMPDIR": str(root / "tmp"),
    "TERM": "xterm-256color",
    "LANG": "en_US.UTF-8",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_CONFIG_DIR": str(root / "config/opencode"),
    "OPENCODE_CONFIG_CONTENT": json.dumps(
        {"autoupdate": False, "share": "disabled", "enabled_providers": []}
    ),
}
config = root / "config/opencode"
config.mkdir()
observer_token = secrets.token_urlsafe(24)
observed = []
condition = threading.Condition()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer " + observer_token:
            self.send_response(401)
            self.end_headers()
            return
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with condition:
            observed.append(data)
            condition.notify_all()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


collector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=collector.serve_forever, daemon=True).start()
env["DUCKTERM_OPENCODE_OBSERVER"] = f"http://127.0.0.1:{collector.server_port}/"
env["DUCKTERM_OPENCODE_OBSERVER_TOKEN"] = observer_token
env["DUCKTERM_HARNESS_GENERATION"] = secrets.token_hex(16)
plugin = pathlib.Path(__file__).resolve().parents[1] / "src/duckterm/hooks/opencode-tui.js"
(config / "tui.json").write_text(
    json.dumps({"plugin": [str(plugin)], "attention": {"enabled": False}})
)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
password = secrets.token_urlsafe(24)
env["OPENCODE_SERVER_PASSWORD"] = password
headers = {
    "Authorization": "Basic " + base64.b64encode(("opencode:" + password).encode()).decode(),
    "Content-Type": "application/json",
    "x-opencode-directory": str(root / "project"),
}


def request(method, path, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


name = "oc-proof-" + secrets.token_hex(5)
native = []
try:
    version = subprocess.check_output(
        [str(binary), "--version"], env=env, cwd=root / "project", text=True, timeout=20
    ).strip()
    assert version == "1.18.34", version
    subprocess.run(
        [
            "tmux",
            "-L",
            name,
            "new-session",
            "-d",
            "-s",
            "proof",
            "-x",
            "120",
            "-y",
            "40",
            shlex.join(
                [str(binary), str(root / "project"), "--hostname", "127.0.0.1", "--port", str(port)]
            ),
        ],
        env=env,
        check=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            health = request("GET", "/global/health")
            if health.get("healthy"):
                break
        except OSError:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("Native server not ready")
    with condition:
        assert condition.wait_for(
            lambda: any(x.get("ready") for x in observed), timeout=10
        ), "TUI not ready"
    for i in range(2):
        session = request("POST", "/session", {"title": "DuckTerm isolated QA " + str(i)})
        native.append(session["id"])
        request("POST", "/tui/select-session", {"sessionID": session["id"]})
        with condition:
            proven = condition.wait_for(
                lambda identity=session["id"]: any(
                    item.get("sessionID") == identity for item in observed
                ),
                timeout=10,
            )
        assert proven, "No TUI selection proof"
    assert all(x["generation"] == env["DUCKTERM_HARNESS_GENERATION"] for x in observed)
    assert all(x["directory"] == str(root / "project") for x in observed)
    assert len({x["pid"] for x in observed}) == 1
    print(
        json.dumps(
            {
                "passed": True,
                "version": version,
                "executable_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "native_ids": native,
                "observed": [
                    {k: v for k, v in item.items() if k != "generation"} for item in observed
                ],
                "isolated_root": str(root),
            }
        ),
        flush=True,
    )
except BaseException:
    screen = subprocess.run(
        ["tmux", "-L", name, "capture-pane", "-p", "-t", "proof"], capture_output=True, text=True
    )
    (root / "screen.txt").write_text(screen.stdout)
    print(
        json.dumps(
            {
                "passed": False,
                "observed": [
                    {k: v for k, v in item.items() if k != "generation"} for item in observed
                ],
                "isolated_root": str(root),
            }
        ),
        flush=True,
    )
    raise
finally:
    try:
        for identity in native:
            request("DELETE", "/session/" + identity)
    finally:
        subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True)
        collector.shutdown()
        collector.server_close()
