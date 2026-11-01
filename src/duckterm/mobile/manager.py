"""Follow local opt-in and network changes without restarting the terminal server."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import ssl
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.helpers import instance
from duckterm.helpers.private_files import private_read, private_write
from duckterm.mobile import network
from duckterm.mobile.bridge import Bridge, start
from duckterm.mobile.cli import address, local_name

if TYPE_CHECKING:
    from duckterm.server import Server


class Manager:
    def __init__(self, server: Server, directory: Path | None = None) -> None:
        self.server = server
        self.directory = directory or instance.home()
        self.bridge: Bridge | None = None
        self.signature: tuple[str, str, str, int] | None = None
        self.status: dict[str, Any] | None = None

    def desired(self) -> tuple[tuple[str, str, str, int], dict[str, Any]] | None:
        raw = private_read(self.directory / "ducky-bridge.json")
        if raw is None:
            return None
        config = json.loads(raw)
        if not isinstance(config, dict) or config.get("version") != 1:
            raise ValueError("Unsupported Ducky bridge configuration")
        host = address(config["address"])
        port = config["port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Invalid Ducky port")
        if config.get("hostname"):
            local_name(config["hostname"])
        if config.get("interface"):
            # Discovery refuses ambiguity, public addresses and virtual adapters.
            # The old address is only a saved identity hint, never a wildcard.
            host = network.detect_address(interface=config["interface"], family=config["family"])
        certificate = private_read(self.directory / "ducky-cert.pem")
        if certificate is None:
            raise ValueError("Ducky identity is unavailable")
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
        signature = (raw, host, fingerprint, (self.directory / "ducky-key.pem").stat().st_mtime_ns)
        return signature, {"address": host, "port": port, "certificate_sha256": fingerprint}

    async def report(self, status: dict[str, Any]) -> None:
        if status != self.status:
            await asyncio.to_thread(
                private_write,
                self.directory / "ducky-status.json",
                json.dumps(
                    {"version": 1, "pid": os.getpid(), "updated_at": int(time.time()), **status}
                ),
            )
            self.status = status

    async def close_listener(self) -> None:
        if self.bridge is not None:
            await self.bridge.close()
            self.bridge = None
        self.signature = None

    async def tick(self) -> None:
        try:
            desired = await asyncio.to_thread(self.desired)
            if desired is None:
                await self.close_listener()
                if self.status is not None:
                    await self.report({"state": "disabled"})
                return
            signature, status = desired
            if self.signature == signature:
                return
            await self.close_listener()
            self.bridge = await start(
                self.server, directory=self.directory, bind_address=status["address"]
            )
            if self.bridge is None:
                return  # Disabled while the new listener was being prepared.
            # A concurrent opt-out/reconfiguration must not leave an old listener
            # advertised as ready. dispatch also checks opt-out before each read.
            if await asyncio.to_thread(self.desired) != desired:
                await self.close_listener()
                return
            self.signature = signature
            await self.report({"state": "ready", **status})
        except (OSError, ValueError, KeyError, TypeError):
            await self.close_listener()
            await self.report({"state": "unavailable"})

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    # An optional listener or damaged local file must never stop
                    # the terminal server. tick retries after the next interval.
                    await self.close_listener()
                await asyncio.sleep(2)
        finally:
            await self.close_listener()
            if self.status is not None:
                with contextlib.suppress(OSError, ValueError):
                    await self.report({"state": "stopped"})
