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

import hashlib
import http.client
import json
import secrets
import shutil
import subprocess
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


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    readonly: bool = False


def create_args(
    run_id: str, *, image: str, limits: Limits, runtime: str, mounts: tuple[Mount, ...] = ()
) -> list[str]:
    """The `docker run` arguments for a run's sandbox. Kept separate so the
    security flags can be checked without docker. The only mounts are the
    run's own directories (autopilot.executor)."""
    binds: list[str] = []
    for m in mounts:
        spec = f"type=bind,source={m.source},target={m.target}" + (
            ",readonly" if m.readonly else ""
        )
        binds += ["--mount", spec]
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
        *binds,
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


CREDS_MOUNT = "/run/duckterm/credentials.json"
STAMP_LABEL = "duckterm.gateway"


def gateway_args(
    name: str, src: Path, creds: Path, env: dict[str, str], stamp: str = ""
) -> list[str]:
    """`docker run` arguments for the gateway. Kept separate so the absence of
    credentials in them can be checked without docker. `stamp` labels what the
    gateway was started with (see Gateway.ensure)."""
    args = [
        "run", "-d", "--name", name, "--restart", "unless-stopped",
        "--label", f"{STAMP_LABEL}={stamp}",
        "-p", "127.0.0.1::8081",
        "-v", f"{src}:/src:ro",
        "--mount", f"type=bind,source={creds},target={CREDS_MOUNT},readonly",
        "--tmpfs", "/var/log/duckterm",
    ]  # fmt: skip
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    return args


@dataclass
class Gateway:
    """The long-lived container running the model proxy. Its control port is
    published on 127.0.0.1 only, for this machine's DuckTerm server."""

    name: str = GATEWAY
    # Docker picks a free port on 127.0.0.1; read back after start.
    control_port: int = 0
    secret_path: Path = field(default_factory=lambda: instance.home() / "autopilot-control-secret")
    creds_path: Path = field(
        default_factory=lambda: instance.home() / "autopilot-gateway-credentials.json"
    )

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
        chatgpt: tuple[str, str] | None = None,
    ) -> None:
        """Start the gateway unless it is running with these credentials and
        this proxy code. A subscription sign-in is refreshed every few days,
        and the proxy holds what it was started with, so a gateway started
        with an older sign-in or older code is replaced. Replacing it ends
        the run tokens it issued, so a run in flight elsewhere loses its
        model access. `upstreams` overrides the model API base URLs
        (DUCKTERM_ANTHROPIC_URL / DUCKTERM_OPENAI_URL / DUCKTERM_CHATGPT_URL)."""
        creds = {"DUCKTERM_PROXY_CONTROL_SECRET": self._secret()}
        if anthropic_key:
            creds["ANTHROPIC_API_KEY"] = anthropic_key
        if openai_key:
            creds["OPENAI_API_KEY"] = openai_key
        if anthropic_oauth_token:
            creds["CLAUDE_CODE_OAUTH_TOKEN"] = anthropic_oauth_token
        if chatgpt:
            creds["CHATGPT_ACCESS_TOKEN"], creds["CHATGPT_ACCOUNT_ID"] = chatgpt
        # The proxy is stdlib-only, so the gateway runs this checkout's
        # package from a read-only mount rather than a built image.
        src = Path(duckterm.__file__).resolve().parent.parent
        env = {"PYTHONPATH": "/src", "DUCKTERM_PROXY_SECRETS": CREDS_MOUNT, **(upstreams or {})}
        code = (src / "duckterm/autopilot/model_proxy.py").read_bytes()
        stamp = hashlib.sha256(
            json.dumps([creds, env, str(src)], sort_keys=True).encode() + code
        ).hexdigest()[:32]
        state = docker(
            "inspect", "-f", f'{{{{.State.Running}}}} {{{{index .Config.Labels "{STAMP_LABEL}"}}}}',
            self.name, check=False,
        )  # fmt: skip
        if state.stdout.split() == ["true", stamp]:
            self._read_port()
            return
        docker("rm", "-f", self.name, check=False)
        # Credentials go in a 0600 file mounted read-only, never `-e`: env
        # values show in the host's process list and in `docker inspect`.
        private_write(self.creds_path, json.dumps(creds))
        args = gateway_args(self.name, src, self.creds_path, env, stamp)
        docker(*args, GATEWAY_IMAGE, "python", "-m", "duckterm.autopilot.model_proxy")
        self._read_port()
        for _ in range(50):
            try:
                self._control("GET", "/runs/none")
                return
            except OSError:
                time.sleep(0.2)
        raise SandboxError(
            "the model proxy did not start: " + docker("logs", self.name, check=False).stderr[-500:]
        )

    def _read_port(self) -> None:
        published = docker("port", self.name, "8081/tcp").stdout.split()[0]
        self.control_port = int(published.rsplit(":", 1)[1])

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
        mounts: tuple[Mount, ...] = (),
    ) -> "Sandbox":
        sandbox = cls(run_id, "runsc" if gvisor_available() else "runc", gateway)
        docker("network", "create", "--internal", f"ap-{run_id}")
        try:
            docker(
                *create_args(
                    run_id,
                    image=image,
                    limits=limits or Limits(),
                    runtime=sandbox.runtime,
                    mounts=mounts,
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
        workdir: str = WORKSPACE,
    ) -> subprocess.CompletedProcess[str]:
        args = ["docker", "exec", "-i", "-w", workdir]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        return subprocess.run(
            [*args, self.name, *argv], input=stdin, capture_output=True, text=True, timeout=timeout
        )

    def trust(self, path: str) -> None:
        """Mark `path` trusted for Claude Code inside this sandbox, so a kit's
        project hooks load there. The sandbox is the run's own; nothing on the
        developer's machine is marked."""
        config = {
            "hasCompletedOnboarding": True,
            "projects": {path: {"hasTrustDialogAccepted": True}},
        }
        proc = self.exec(["sh", "-c", "cat > $HOME/.claude.json"], stdin=json.dumps(config))
        if proc.returncode != 0:
            raise SandboxError(f"could not write the trust record: {proc.stderr.strip()}")

    def destroy(self) -> None:
        docker("rm", "-f", self.name, check=False)
        docker("network", "disconnect", "-f", f"ap-{self.run_id}", self.gateway, check=False)
        docker("network", "rm", f"ap-{self.run_id}", check=False)


def docker_available() -> bool:
    return (
        shutil.which("docker") is not None
        and docker("info", check=False, timeout=20).returncode == 0
    )
