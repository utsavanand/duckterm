"""Outbound, service-owned SSH connection to the workspace coordinator."""

from __future__ import annotations

import asyncio
import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any

from duckterm.core.session_api import APIError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> None:
        return None


class Transport:
    def __init__(self, target: str, remote_port: int = 4300):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._@-]{0,254}", target):
            raise APIError(400, "Choose a valid configured SSH computer")
        if type(remote_port) is not int or not 1 <= remote_port <= 65535:
            raise APIError(400, "Invalid coordinator service port")
        self.target, self.remote_port = target, remote_port
        self.process: asyncio.subprocess.Process | None = None
        self.port = 0

    async def start(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.process = await asyncio.create_subprocess_exec(
            "/usr/bin/ssh",
            "-N",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ControlMaster=no",
            "-o",
            "ControlPath=none",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-L",
            f"127.0.0.1:{self.port}:127.0.0.1:{self.remote_port}",
            "--",
            self.target,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(40):
            if self.process.returncode is not None:
                raise APIError(503, "Could not establish the verified coordinator connection")
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", self.port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(0.25)
        await self.close()
        raise APIError(503, "Coordinator connection timed out")

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()

    async def exchange(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.request("exchange", "Computer", token, payload)

    async def request(
        self, route: str, kind: str, token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        await self.start()
        port = self.port

        def request() -> dict[str, Any]:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/v1/collaboration/{route}",
                data=json.dumps(payload).encode(),
                headers={"Authorization": f"{kind} {token}", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.build_opener(NoRedirect()).open(req, timeout=10) as response:
                    body = response.read(4 * 1024 * 1024 + 1)
            except urllib.error.HTTPError as exc:
                # Never include response bodies or credentials in transport errors.
                raise APIError(
                    exc.code, "Coordinator rejected the exchange; check pairing and protocol"
                ) from exc
            if len(body) > 4 * 1024 * 1024:
                raise APIError(502, "Coordinator response exceeds its limit")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise APIError(502, "Invalid coordinator response")
            return result

        return await asyncio.to_thread(request)
