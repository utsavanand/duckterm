"""Execution-only connector service. Admin changes arrive through root-owned files.

Clients authenticate with workspace certificates. They can select a configured
connector and speak MCP, but cannot select credentials, commands or permissions.
"""

import asyncio
import contextlib
import json
import os
import signal
import ssl
from pathlib import Path
from typing import Any

from duckterm import connectors
from duckterm.helpers.private_files import private_write
from duckterm.helpers.secret_store import GcpSecretStore, SecretStore

MAX_CONNECTIONS = 8


def read_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError("Invalid connector service configuration")
    return value


def command(name: str, credential: dict[str, str], write: bool) -> tuple[list[str], dict[str, str]]:
    # No inherited cloud credentials, user CLI logins, proxy variables or source
    # overrides. The service's home contains no workspace code or harness config.
    env = {
        "HOME": os.environ["HOME"],
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
    }
    if name == "github":
        env["GITHUB_PERSONAL_ACCESS_TOKEN"] = credential["token"]
        return ["github-mcp-server", "stdio"], env
    if name == "railway":
        env["RAILWAY_API_TOKEN"] = credential["token"]
        return ["railway", "mcp", "local"], env
    if name == "porkbun":
        env.update(
            PORKBUN_API_KEY=credential["token"],
            PORKBUN_SECRET_KEY=credential["secret"],
            PORKBUN_GET_MUDDY="false",
        )
        return ["porkbun-mcp"] + (["--get-muddy"] if write else []), env
    raise ValueError("Unknown connector")


async def terminate(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), 2)
    # Kill remaining descendants too, including children ignoring SIGTERM.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    await proc.wait()


class Broker:
    def __init__(self, config: Path, store: SecretStore | None = None) -> None:
        self.config = config
        self.store = store or GcpSecretStore()
        self.active = 0

    def permitted(self, workspace: str, name: str) -> dict[str, Any]:
        config = read_config(self.config)
        if name not in config.get("workspaces", {}).get(workspace, []):
            raise ValueError("Connector not authorized for this workspace")
        entry = config.get("connectors", {}).get(name, {})
        if not entry.get("enabled"):
            raise ValueError("Connector disabled")
        return dict(entry)

    def statuses(self, workspace: str) -> list[dict[str, Any]]:
        config = read_config(self.config)
        allowed = config.get("workspaces", {}).get(workspace, [])
        return [
            {
                "name": name,
                "enabled": bool(config.get("connectors", {}).get(name, {}).get("enabled")),
                "identity": config.get("connectors", {}).get(name, {}).get("identity"),
                "write_access": bool(
                    config.get("connectors", {}).get(name, {}).get("write_access")
                ),
            }
            for name in connectors.NAMES
            if name in allowed
        ]

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        certificate = writer.get_extra_info("peercert") or {}
        names = [
            value
            for group in certificate.get("subject", ())
            for key, value in group
            if key == "commonName"
        ]
        workspace = names[0] if len(names) == 1 else ""
        proc = None
        tasks: list[asyncio.Task[Any]] = []
        counted = False
        try:
            if not workspace or self.active >= MAX_CONNECTIONS:
                raise ValueError("Connector service unavailable")
            hello = json.loads(await asyncio.wait_for(reader.readline(), 5))
            if not isinstance(hello, dict) or set(hello) != {"name"}:
                raise ValueError("Expected connector name")
            name = hello["name"]
            if not isinstance(name, str):
                raise ValueError("Expected connector name")
            if name == "status":
                writer.write(json.dumps({"connectors": self.statuses(workspace)}).encode() + b"\n")
                await writer.drain()
                return
            entry = self.permitted(workspace, name)
            self.active += 1
            counted = True
            credential = await asyncio.to_thread(self.store.read, entry["reference"])
            # Recheck after the network fetch: disable must win launch races.
            if self.permitted(workspace, name) != entry:
                raise ValueError("Connector configuration changed")
            argv, env = command(name, credential, bool(entry.get("write_access")))
            proc = await asyncio.create_subprocess_exec(
                *argv,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            del credential, env
            writer.write(b'{"ready":true}\n')
            await writer.drain()
            assert proc.stdin is not None and proc.stdout is not None

            async def pump(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()

            async def watch() -> None:
                while self.permitted(workspace, name) == entry:
                    await asyncio.sleep(0.2)

            tasks = [
                asyncio.create_task(pump(reader, proc.stdin)),
                asyncio.create_task(pump(proc.stdout, writer)),
                asyncio.create_task(watch()),
                asyncio.create_task(proc.wait()),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, ValueError, KeyError, RuntimeError, TimeoutError):
            # Never expose subprocess logs, secret refs or provider error bodies.
            with contextlib.suppress(ConnectionError):
                writer.write(
                    b'{"error":"Connector unavailable or disabled; contact the administrator"}\n'
                )
                await writer.drain()
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if proc is not None:
                await terminate(proc)
            if counted:
                self.active -= 1
            writer.close()
            with contextlib.suppress(ConnectionError, ssl.SSLError):
                await writer.wait_closed()


async def serve(config: Path) -> None:
    settings = read_config(config)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(settings["certificate"], settings["private_key"])
    tls.load_verify_locations(settings["client_ca"])
    tls.verify_mode = ssl.CERT_REQUIRED
    broker = Broker(config)
    server = await asyncio.start_server(
        broker.handle,
        settings.get("bind", "0.0.0.0"),
        settings.get("port", 8443),
        ssl=tls,
        limit=4096,
        ssl_handshake_timeout=5,
    )
    async with server:
        await server.serve_forever()


def admin(config: Path, name: str, version: int | None, write: bool, disable: bool) -> None:
    """Administrator-only CLI, invoked through an administrative SSH connection.

    The broker user can read configuration but cannot change it. Agents have no
    access to this host's SSH identity or to its administrative filesystem.
    """
    if os.getuid() != 0:
        raise RuntimeError("Connector administration requires the administrator account")
    if name not in connectors.NAMES:
        raise ValueError("Unknown connector")
    settings = read_config(config)
    entries = settings.setdefault("connectors", {})
    if disable:
        entries.setdefault(name, {})["enabled"] = False
    else:
        if version is None or version < 1:
            raise ValueError("Supply an enabled numeric Secret Manager version")
        reference = f"projects/{settings['project']}/secrets/duckterm-{name}/versions/{version}"
        credential = GcpSecretStore().read(reference)
        if name == "github":
            identity = connectors.github_identity(credential["token"])
        elif name == "porkbun":
            if not connectors.porkbun_keys_valid(credential["token"], credential["secret"]):
                raise RuntimeError("Porkbun rejected these credentials")
            identity = "API keys verified; account identity unavailable"
        else:
            import subprocess

            _, env = command(name, credential, False)
            proc = subprocess.run(
                ["railway", "whoami"], env=env, capture_output=True, text=True, timeout=15
            )
            if proc.returncode:
                raise RuntimeError("Railway rejected the selected account token")
            identity = proc.stdout.strip()[:200] or "Railway account token verified"
        entries[name] = {
            "enabled": True,
            "reference": reference,
            "identity": identity,
            "write_access": name == "porkbun" and write,
        }
    # Preserve the broker's read-only group access after atomic replacement.
    group = config.stat().st_gid
    private_write(config, json.dumps(settings, indent=2) + "\n")
    os.chown(config, 0, group)
    config.chmod(0o640)
    print(json.dumps({"name": name, "enabled": not disable}))
