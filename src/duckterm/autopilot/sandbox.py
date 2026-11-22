"""One isolated container per autopilot run, driven through the docker CLI.

Each run gets its own internal network. The only other member of that network
is the gateway container, which runs the model proxy, so a step can reach the
model through its run token and nothing else: no internet, no cloud metadata
service, no other run. The gateway holds the real API keys; the sandbox never
does.

gVisor (`runsc`) is used when docker has it. Docker Desktop on macOS does not,
so development runs there use `runc` and record it (autopilot spec §5; owner
chose Docker on the Mac first, 2026-11-22).
"""

import http.client
import json
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckterm
from duckterm.helpers import instance
from duckterm.helpers.private_files import private_read, private_write

IMAGE = "duckterm/autopilot-base:dev"
GATEWAY = "duckterm-autopilot-gateway"
GATEWAY_IMAGE = "python:3.13-slim"
PROXY_ALIAS = "model-proxy"
WORKSPACE = "/workspace"


class SandboxError(RuntimeError):
    pass


def docker(
    *args: str, check: bool = True, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise SandboxError(f"docker {args[0]} failed: {proc.stderr.strip()}")
    return proc


def gvisor_available() -> bool:
    out = docker("info", "--format", "{{json .Runtimes}}", check=False).stdout
    return "runsc" in out


@dataclass
class Limits:
    cpus: str = "4"
    memory: str = "8g"
    pids: int = 4096


def create_args(run_id: str, *, image: str, limits: Limits, runtime: str) -> list[str]:
    """The `docker run` arguments for a run's sandbox. Kept separate so the
    security flags can be checked without docker."""
    return [
        "run", "-d",
        "--name", f"sb-{run_id}",
        "--network", f"ap-{run_id}",
        "--runtime", runtime,
        "--user", "1000:1000",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(limits.pids),
        "--cpus", limits.cpus,
        "--memory", limits.memory,
        "--workdir", WORKSPACE,
        "--label", f"duckterm.autopilot.run={run_id}",
        image, "sleep", "infinity",
    ]  # fmt: skip


def harness_env(token: str) -> dict[str, str]:
    """What a step's harness needs to reach the model through the proxy. The
    run token is the only credential; Codex reads it through its
    `model_providers.duckterm.env_key`, baked into the image's config."""
    return {
        "ANTHROPIC_BASE_URL": f"http://{PROXY_ALIAS}:8080",
        "ANTHROPIC_AUTH_TOKEN": token,
        "DUCKTERM_RUN_TOKEN": token,
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }


@dataclass
class Gateway:
    """The long-lived container running the model proxy. Its control port is
    published on 127.0.0.1 only, for this machine's DuckTerm server."""

    name: str = GATEWAY
    control_port: int = 4381
    secret_path: Path = field(default_factory=lambda: instance.home() / "autopilot-control-secret")

    def _secret(self) -> str:
        secret = private_read(self.secret_path)
        if not secret:
            secret = secrets.token_urlsafe(32)
            private_write(self.secret_path, secret)
        return secret.strip()

    def ensure(
        self,
        *,
        anthropic_key: str | None,
        openai_key: str | None,
        upstreams: dict[str, str] | None = None,
        anthropic_oauth_token: str | None = None,
    ) -> None:
        """Start the gateway unless it is running. `upstreams` overrides the
        model API base URLs (DUCKTERM_ANTHROPIC_URL / DUCKTERM_OPENAI_URL)."""
        state = docker("inspect", "-f", "{{.State.Running}}", self.name, check=False)
        if state.stdout.strip() == "true":
            return
        docker("rm", "-f", self.name, check=False)
        # The proxy is stdlib-only, so the gateway runs this checkout's
        # package from a read-only mount rather than a built image.
        src = Path(duckterm.__file__).resolve().parent.parent
        env = {"DUCKTERM_PROXY_CONTROL_SECRET": self._secret(), "PYTHONPATH": "/src"}
        if anthropic_key:
            env["ANTHROPIC_API_KEY"] = anthropic_key
        if openai_key:
            env["OPENAI_API_KEY"] = openai_key
        if anthropic_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = anthropic_oauth_token
        env.update(upstreams or {})
        args = ["run", "-d", "--name", self.name, "--restart", "unless-stopped",
                "-p", f"127.0.0.1:{self.control_port}:8081",
                "-v", f"{src}:/src:ro", "--tmpfs", "/var/log/duckterm"]  # fmt: skip
        for key, value in env.items():
            args += ["-e", f"{key}={value}"]
        docker(*args, GATEWAY_IMAGE, "python", "-m", "duckterm.autopilot.model_proxy")
        for _ in range(50):
            try:
                self._control("GET", "/runs/none")
                return
            except OSError:
                time.sleep(0.2)
        raise SandboxError(
            "the model proxy did not start: " + docker("logs", self.name, check=False).stderr[-500:]
        )

    def _control(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object]]:
        conn = http.client.HTTPConnection("127.0.0.1", self.control_port, timeout=10)
        try:
            conn.request(
                method,
                path,
                body=json.dumps(body) if body else None,
                headers={"authorization": f"Bearer {self._secret()}"},
            )
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()
        return resp.status, json.loads(raw) if raw else {}

    def mint(self, run_id: str, *, budget: int, ttl: float) -> str:
        status, body = self._control(
            "POST", "/runs", {"run_id": run_id, "budget": budget, "ttl": ttl}
        )
        if status != 201:
            raise SandboxError(f"model proxy refused to mint a run token: {body}")
        return str(body["token"])

    def usage(self, token: str) -> dict[str, object]:
        return self._control("GET", f"/runs/{token}")[1]

    def revoke(self, token: str) -> None:
        self._control("DELETE", f"/runs/{token}")


@dataclass
class Sandbox:
    run_id: str
    runtime: str
    gateway: str = GATEWAY

    @property
    def name(self) -> str:
        return f"sb-{self.run_id}"

    @classmethod
    def create(
        cls,
        run_id: str,
        *,
        image: str = IMAGE,
        limits: Limits | None = None,
        gateway: str = GATEWAY,
    ) -> "Sandbox":
        sandbox = cls(run_id, "runsc" if gvisor_available() else "runc", gateway)
        docker("network", "create", "--internal", f"ap-{run_id}")
        try:
            docker(
                *create_args(
                    run_id, image=image, limits=limits or Limits(), runtime=sandbox.runtime
                )
            )
            docker("network", "connect", "--alias", PROXY_ALIAS, f"ap-{run_id}", gateway)
        except SandboxError:
            sandbox.destroy()
            raise
        return sandbox

    def exec(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float = 2700,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = ["docker", "exec", "-i", "-w", WORKSPACE]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        return subprocess.run(
            [*args, self.name, *argv], input=stdin, capture_output=True, text=True, timeout=timeout
        )

    def load_repo(self, repo: Path, ref: str, branch: str) -> None:
        """Copy `ref` (a branch or tag) of a local repository into the workspace
        as `branch`.
        A bundle carries history but no remotes, credentials or hooks."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "repo.bundle"
            subprocess.run(
                ["git", "-C", str(repo), "bundle", "create", str(bundle), ref],
                check=True,
                capture_output=True,
            )
            docker("cp", str(bundle), f"{self.name}:/tmp/repo.bundle")
        for argv in (
            ["git", "init", "-q", "."],
            ["git", "fetch", "-q", "/tmp/repo.bundle", ref],
            ["git", "checkout", "-q", "-b", branch, "FETCH_HEAD"],
        ):
            proc = self.exec(argv, timeout=300)
            if proc.returncode != 0:
                raise SandboxError(f"{' '.join(argv)} failed: {proc.stderr.strip()}")

    def export_branch(self, branch: str, repo: Path) -> str:
        """Bring the run branch back to the local repository; returns its head.
        Stands in for the git proxy's push until that exists."""
        proc = self.exec(["git", "bundle", "create", "/tmp/out.bundle", branch])
        if proc.returncode != 0:
            raise SandboxError(f"bundling {branch} failed: {proc.stderr.strip()}")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.bundle"
            docker("cp", f"{self.name}:/tmp/out.bundle", str(out))
            subprocess.run(
                ["git", "-C", str(repo), "fetch", "-q", str(out), f"+{branch}:{branch}"],
                check=True,
                capture_output=True,
            )
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", branch],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def destroy(self) -> None:
        docker("rm", "-f", self.name, check=False)
        docker("network", "disconnect", "-f", f"ap-{self.run_id}", self.gateway, check=False)
        docker("network", "rm", f"ap-{self.run_id}", check=False)


def docker_available() -> bool:
    return (
        shutil.which("docker") is not None
        and docker("info", check=False, timeout=20).returncode == 0
    )
