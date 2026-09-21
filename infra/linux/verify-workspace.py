"""Run as duckterm: start or verify two disposable remote persistence probes."""

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:4300"
HOME_DIR = Path.home()
TOKEN = (HOME_DIR / ".duckterm" / "token").read_text().strip()
KEYS = ["remote-qa-one", "remote-qa-two"]


def request(path: str, body: dict[str, object] | None = None) -> dict[str, object]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"x-duckterm-token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


if sys.argv[1] == "start":
    for key in KEYS:
        request(
            "/sessions/launch",
            {
                "command": "python3 -u -c 'import time; print(\"REMOTE_QA_READY\", flush=True); time.sleep(43200)'",
                "cwd": str(HOME_DIR / "projects"),
                "in_terminal": False,
                "session_key": key,
            },
        )
    pids = subprocess.check_output(
        ["tmux", "-L", "duckterm", "list-panes", "-a", "-F", "#{session_name}:#{pane_pid}"],
        text=True,
    )
    pids = (
        "\n".join(
            line
            for line in pids.splitlines()
            if line.split(":")[0] in {"rd_" + key for key in KEYS}
        )
        + "\n"
    )
    (HOME_DIR / ".duckterm" / "remote-qa-pids").write_text(pids)
    print(json.dumps({"started": KEYS, "panes": pids.splitlines()}))
elif sys.argv[1] == "verify":
    pids = subprocess.check_output(
        ["tmux", "-L", "duckterm", "list-panes", "-a", "-F", "#{session_name}:#{pane_pid}"],
        text=True,
    )
    pids = (
        "\n".join(
            line
            for line in pids.splitlines()
            if line.split(":")[0] in {"rd_" + key for key in KEYS}
        )
        + "\n"
    )
    assert pids == (HOME_DIR / ".duckterm" / "remote-qa-pids").read_text()
    rows = request("/sessions")["sessions"]
    found = {row["session_key"]: row for row in rows if row["session_key"] in KEYS}
    assert len(found) == 2
    assert all(row["state"] not in ("terminated", "stopped", "archived") for row in found.values())
    print(
        json.dumps({"same_processes": True, "sessions": {k: v["state"] for k, v in found.items()}})
    )
else:
    raise SystemExit("Use start or verify")
