"""Autopilot sandboxes: the security flags, and a real run of Claude Code inside
one, reaching a stub model only through the gateway's proxy.

The docker test is opt-in (DUCKTERM_DOCKER_TESTS=1) and needs the base image:
docker build -t duckterm/autopilot-base:dev infra/autopilot
"""

import json
import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from duckterm.autopilot import sandbox
from duckterm.autopilot.executor import RunDirs, prepare
from duckterm.autopilot.sandbox import Gateway, Limits, Mount, Sandbox, create_args, harness_env

REAL_KEY = "sk-ant-REAL-sentinel-5e1f"


def test_sandbox_drops_privileges_and_mounts_nothing() -> None:
    args = create_args("r1", image="img", limits=Limits(), runtime="runsc")
    joined = " ".join(args)
    for flag in (
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--user 1000:1000",
        "--network ap-r1",
        "--runtime runsc",
        "--pids-limit 4096",
    ):
        assert flag in joined
    assert "-v" not in args and "--mount" not in args and "--privileged" not in args
    assert "docker.sock" not in joined


def test_run_directories_are_the_only_mounts_and_git_is_read_only(tmp_path: Path) -> None:
    dirs = RunDirs(tmp_path / "run")
    args = create_args("r1", image="img", limits=Limits(), runtime="runc", mounts=dirs.mounts())
    binds = [args[i + 1] for i, a in enumerate(args) if a == "--mount"]
    p = dirs.project
    assert binds == [
        f"type=bind,source={p},target={p}",
        f"type=bind,source={p}/.git,target={p}/.git,readonly",
        f"type=bind,source={p}/.duckterm/flows,target={p}/.duckterm/flows,readonly",
        f"type=bind,source={dirs.scratch},target={p}/.duckterm/runs",
    ]
    assert isinstance(dirs.mounts()[0], Mount)


def test_harness_env_carries_only_the_run_token() -> None:
    env = harness_env("dtrun-abc")
    assert env["ANTHROPIC_AUTH_TOKEN"] == env["DUCKTERM_RUN_TOKEN"] == "dtrun-abc"
    assert "ANTHROPIC_API_KEY" not in env and "OPENAI_API_KEY" not in env


class StubModel(BaseHTTPRequestHandler):
    """Answers Claude Code's Messages calls, and only with the real key."""

    calls: list[dict[str, Any]]
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        ok = self.headers.get("x-api-key") == REAL_KEY
        self.calls.append({"path": self.path, "key_ok": ok})
        if not ok:
            return self._send(401, b'{"error":"bad key"}', "application/json")
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            return self._send(200, b'{"input_tokens": 10}', "application/json")
        usage = {"input_tokens": 120, "output_tokens": 7}
        message = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "m"),
            "content": [{"type": "text", "text": "SANDBOX_OK"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": usage,
        }
        if not body.get("stream"):
            return self._send(200, json.dumps(message).encode(), "application/json")
        events = [
            {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "SANDBOX_OK"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 7},
            },
            {"type": "message_stop"},
        ]
        sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
        self._send(200, sse, "text/event-stream")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[Gateway, Sandbox, RunDirs, list[dict[str, Any]]]]:
    if os.environ.get("DUCKTERM_DOCKER_TESTS") != "1" or not sandbox.docker_available():
        pytest.skip("set DUCKTERM_DOCKER_TESTS=1 with docker running")
    calls: list[dict[str, Any]] = []
    stub = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (StubModel,), {"calls": calls}))
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    gateway = Gateway(
        name="duckterm-autopilot-gateway-test",
        secret_path=tmp_path / "control-secret",
    )
    gateway.ensure(
        anthropic_key=REAL_KEY,
        openai_key=None,
        upstreams={
            "DUCKTERM_ANTHROPIC_URL": f"http://host.docker.internal:{stub.server_address[1]}"
        },
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    git = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("hello\n")
    subprocess.run([*git, "add", "README.md"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    run_id = "test-" + os.urandom(3).hex()
    dirs = prepare(str(repo), "main", f"autopilot/{run_id}", tmp_path / "run")
    (dirs.project / ".duckterm" / "runs" / "r1").mkdir(parents=True)
    (dirs.project / ".duckterm" / "runs" / "r1" / "run.json").write_text('{"state": "running"}')
    box = Sandbox.create(run_id, gateway=gateway.name, mounts=dirs.mounts())
    try:
        yield gateway, box, dirs, calls
    finally:
        box.destroy()
        sandbox.docker("rm", "-f", gateway.name, check=False)
        stub.shutdown()


def test_claude_runs_in_a_sandbox_through_the_proxy_and_nothing_else(world: Any) -> None:
    gateway, box, dirs, calls = world
    project = str(dirs.project)
    assert box.exec(["cat", "README.md"], workdir=project).stdout == "hello\n"

    token = gateway.mint(box.run_id, budget=1_000_000, ttl=600)
    run = box.exec(
        ["claude", "-p", "say ok", "--output-format", "json"],
        env=harness_env(token),
        timeout=180,
        workdir=project,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["result"] == "SANDBOX_OK"
    assert calls and all(c["key_ok"] for c in calls)
    assert gateway.usage(token)["used"] > 0

    # Nothing but the proxy is reachable: not the model API, not the host, not metadata.
    probe = (
        "const t=u=>fetch(u,{signal:AbortSignal.timeout(4000)}).then(()=>'reached',()=>'blocked');"
        "Promise.all(['https://api.anthropic.com/v1/messages','http://host.docker.internal:4300/',"
        "'http://169.254.169.254/']. map(t)).then(r=>console.log(r.join(',')))"
    )
    assert box.exec(["node", "-e", probe], timeout=30).stdout.strip() == "blocked,blocked,blocked"

    # The workspace is shared with the host; .git, the installed flows and the
    # run records are not writable from the sandbox.
    box.exec(["sh", "-c", "echo change >> README.md"], workdir=project)
    assert (dirs.project / "README.md").read_text() == "hello\nchange\n"
    for path in (".git/hooks/pre-commit", ".duckterm/flows/planted.json"):
        assert box.exec(["sh", "-c", f"echo x > {path}"], workdir=project).returncode != 0
        assert not (dirs.project / path).exists()
    forged = box.exec(
        ["sh", "-c", "cat .duckterm/runs/r1/run.json; echo forged > .duckterm/runs/r1/run.json"],
        workdir=project,
    )
    assert "running" not in forged.stdout
    assert (dirs.project / ".duckterm/runs/r1/run.json").read_text() == '{"state": "running"}'

    # A revoked token ends the step at once instead of retrying.
    gateway.revoke(token)
    refused = box.exec(
        ["claude", "-p", "say ok", "--output-format", "json"], env=harness_env(token), timeout=60
    )
    assert refused.returncode == 1

    # The real key is nowhere in the sandbox.
    inspect = sandbox.docker("inspect", box.name).stdout
    exported = subprocess.run(
        f"docker export {box.name} | grep -a -c {REAL_KEY}",
        shell=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert REAL_KEY not in inspect and exported == "0"


def test_gateway_credentials_go_in_a_private_mounted_file_never_in_docker_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def docker(*args: str, check: bool = True, **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        out = "false" if args[0] == "inspect" else "127.0.0.1:55001" if args[0] == "port" else ""
        return subprocess.CompletedProcess(list(args), 0, out, "")

    monkeypatch.setattr(sandbox, "docker", docker)
    monkeypatch.setattr(sandbox.Gateway, "_control", lambda self, *a: {})
    gw = sandbox.Gateway(name="gw", secret_path=tmp_path / "s", creds_path=tmp_path / "c.json")
    gw.ensure(anthropic_key="sk-ant-SECRET", openai_key=None, anthropic_oauth_token="oauth-SECRET")
    (run,) = [c for c in calls if c[0] == "run"]
    flat = " ".join(run)
    for secret in ("sk-ant-SECRET", "oauth-SECRET", (tmp_path / "s").read_text().strip()):
        assert secret not in flat
    assert f"source={tmp_path / 'c.json'},target={sandbox.CREDS_MOUNT},readonly" in flat
    creds = json.loads((tmp_path / "c.json").read_text())
    assert creds["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-SECRET"
    assert (tmp_path / "c.json").stat().st_mode & 0o077 == 0
