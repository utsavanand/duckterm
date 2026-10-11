"""Owned native process setup and authenticated TUI observations.

All generated configuration is private to this DuckTerm launch. User config
and provider credentials are neither rewritten nor copied. The adapter stays
unregistered until supervisor recovery and lifecycle acceptance are complete.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from duckterm.helpers.private_files import private_read, private_write
from duckterm.persistence import opencode_launch as journal
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes import native_history
from duckterm.runtimes.opencode_identity import Selection
from duckterm.runtimes.opencode_prepare import prepare
from duckterm.runtimes.opencode_protocol import VERSION, NativeClient, OpenCodeError
from duckterm.runtimes.opencode_snapshot import snapshot


class OpenCodeProcess:
    allow_automation = False

    def __init__(
        self,
        binary: str,
        model: str,
        cwd: Path,
        key: str,
        prompt: str,
        env: dict[str, str],
        history: HistoryStore,
    ) -> None:
        self.history = history
        self.key = key
        self.directory = str(cwd.resolve())
        self.prompt = prompt
        self.model = model
        self.base_env = {**os.environ, **env}
        self.generation = secrets.token_hex(16)
        self.token = secrets.token_urlsafe(32)
        self.password = secrets.token_urlsafe(32)
        self._server: asyncio.Server | None = None
        self._latest: dict[str, Any] | None = None
        self._received_at = 0.0
        self._selection: Selection | None = None
        self._pid: int | None = None
        self._connections: set[asyncio.StreamWriter] = set()
        self._closed = False
        self._recovering = False
        self._monitor: asyncio.Task[None] | None = None
        self._on_status: Callable[[str], None] | None = None
        self._status: str | None = None
        self._root = (
            history.session_api.credential_dir.parent
            / "opencode"
            / hashlib.sha256(key.encode()).hexdigest()
        )
        data = (
            Path(
                self.base_env.get("XDG_DATA_HOME")
                or str(Path(self.base_env.get("HOME") or str(Path.home())) / ".local/share")
            )
            / "opencode"
        )
        database = self.base_env.get("OPENCODE_DB") or "opencode.db"
        if database == ":memory:":
            raise OpenCodeError("OpenCode needs a persistent native store for recovery")
        self.database = (
            Path(database) if Path(database).is_absolute() else data / database
        ).resolve()
        self.argv = [binary, self.directory, "--hostname", "127.0.0.1"]
        if model:
            self.argv.extend(["--model", model])
        self.env: dict[str, str] = {}
        self.client: NativeClient | None = None

    async def configure(self) -> None:
        await self._verify_binary()
        if self.base_env.get("OPENCODE_TUI_CONFIG"):
            raise OpenCodeError("Custom OPENCODE_TUI_CONFIG is not supported by this adapter yet")
        if self._root.is_symlink() or self._root.parent.is_symlink():
            raise OpenCodeError("Native launch storage cannot be a symbolic link")
        if self._root.exists():
            raise OpenCodeError(
                "This card has saved native preparation; recover it before relaunch"
            )
        self._root.mkdir(parents=True, mode=0o700)
        try:
            self._server = await asyncio.start_server(self._observe, "127.0.0.1", 0, limit=16384)
            observer_port = self._server.sockets[0].getsockname()[1]
            # OpenCode itself must bind its native server. The launch credential
            # and native observer reject a foreign process racing this port.
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                native_port = reservation.getsockname()[1]
            config = self._root / "tui.json"
            plugin = Path(__file__).resolve().parent.parent / "hooks/opencode-tui.js"
            private_write(config, json.dumps({"plugin": [str(plugin)]}))
            self.env = {
                "OPENCODE_TUI_CONFIG": str(config),
                "OPENCODE_DB": str(self.database),
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_SERVER_PASSWORD": self.password,
                "DUCKTERM_OPENCODE_OBSERVER": f"http://127.0.0.1:{observer_port}/observation",
                "DUCKTERM_OPENCODE_OBSERVER_TOKEN": self.token,
                "DUCKTERM_HARNESS_GENERATION": self.generation,
            }
            self.argv.extend(["--port", str(native_port)])
            self.client = NativeClient(native_port, self.password, self.directory)
            self._save_connection(observer_port, native_port, None)
        except BaseException:
            await self.close()
            raise

    async def _verify_binary(self) -> None:
        child = await asyncio.create_subprocess_exec(
            self.argv[0],
            "--version",
            cwd=self.directory,
            env=self.base_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(5):
                assert child.stdout is not None
                output = await child.stdout.read(128)
                if output.strip() != VERSION.encode():
                    raise OpenCodeError("This adapter requires verified OpenCode " + VERSION)
                if await child.wait() != 0:
                    raise OpenCodeError("Could not verify the installed OpenCode version")
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()

    @classmethod
    async def recover(
        cls, cwd: Path, key: str, env: dict[str, str], history: HistoryStore
    ) -> OpenCodeProcess:
        process = cls("", "", cwd, key, "", env, history)
        if process._root.is_symlink() or process._root.parent.is_symlink():
            raise OpenCodeError("Native launch storage cannot be a symbolic link")
        raw = private_read(process._root / "connection.json")
        if raw is None or len(raw) > 16384:
            raise OpenCodeError("Native launch connection is missing or invalid")
        try:
            saved = json.loads(raw)
            state = journal.read(history._conn, key, saved["generation"])
            if (
                saved["directory"] != process.directory
                or state.directory != process.directory
                or state.pid != saved["pid"]
                or type(saved["pid"]) is not int
                or not isinstance(saved["observer_token"], str)
                or not saved["observer_token"]
                or not isinstance(saved["database"], str)
                or type(saved["observer_port"]) is not int
                or not 0 < saved["observer_port"] < 65536
            ):
                raise OpenCodeError("Native recovery scope does not match this card")
            process.database = Path(saved["database"])
            process.generation = state.generation
            process.model = state.model
            process.prompt = state.prompt
            process.token = saved["observer_token"]
            process.password = saved["native_password"]
            process._pid = state.pid
            process.client = NativeClient(saved["native_port"], process.password, process.directory)
        except (KeyError, TypeError, ValueError) as exc:
            raise OpenCodeError("Native recovery metadata is invalid") from exc
        process._recovering = True
        # The native child retains this exact endpoint in its environment.
        # Never pick another port or kill a process which already owns it.
        process._server = await asyncio.start_server(
            process._observe, "127.0.0.1", saved["observer_port"], limit=16384
        )
        return process

    def _save_connection(self, observer_port: int, native_port: int, pid: int | None) -> None:
        private_write(
            self._root / "connection.json",
            json.dumps(
                {
                    "generation": self.generation,
                    "directory": self.directory,
                    "database": str(self.database),
                    "observer_port": observer_port,
                    "native_port": native_port,
                    "observer_token": self.token,
                    "native_password": self.password,
                    "pid": pid,
                }
            ),
        )

    async def _observe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closed or len(self._connections) >= 16:
            writer.close()
            return
        self._connections.add(writer)
        status = "400 Bad Request"
        try:
            async with asyncio.timeout(2):
                header = await reader.readuntil(b"\r\n\r\n")
                if len(header) > 8192:
                    raise OpenCodeError("Oversized observer headers")
                lines = header.decode("ascii").split("\r\n")
                if lines[0] != "POST /observation HTTP/1.1":
                    raise OpenCodeError("Invalid observer request")
                headers: dict[str, str] = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    name, value = line.split(":", 1)
                    if name.lower() in headers:
                        raise OpenCodeError("Duplicate observer header")
                    headers[name.lower()] = value.strip()
                if not hmac.compare_digest(
                    headers.get("authorization", ""), "Bearer " + self.token
                ):
                    status = "401 Unauthorized"
                    raise OpenCodeError("Invalid observer credential")
                size = int(headers.get("content-length", "0"))
                if not 0 < size <= 8192 or "transfer-encoding" in headers:
                    raise OpenCodeError("Invalid observer payload bound")
                event = json.loads(await reader.readexactly(size))
                self._accept(event)
                status = "200 OK"
        except (ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}".encode()
            )
            with contextlib.suppress(OSError):
                await writer.drain()
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self._connections.discard(writer)

    def _accept(self, event: Any) -> None:
        if (
            not isinstance(event, dict)
            or event.get("version") != VERSION
            or event.get("generation") != self.generation
            or event.get("directory") != self.directory
            or type(event.get("pid")) is not int
            or event["pid"] <= 0
            or (self._pid is not None and event["pid"] != self._pid)
            or type(event.get("sequence")) is not int
            or event["sequence"] <= (self._latest or {}).get("sequence", 0)
        ):
            raise OpenCodeError("Foreign or stale native observation")
        if self._selection is not None:
            self._selection = self._selection.observe(event)
            if self._selection.contested:
                journal.read(self.history._conn, self.key, self.generation)
                control = self.history.restart_control(self.key)
                control["native_binding"]["contested"] = True
                self.history.set_restart_control(self.key, control)
        self._latest = event
        self._received_at = time.monotonic()

    async def selection(self, state: journal.Launch) -> Selection:
        if self._selection is None:
            assert state.session_id is not None
            self._selection = Selection(
                state.generation, state.directory, state.pid, state.session_id
            )
            if self._latest:
                self._selection = self._selection.observe(self._latest, now=self._received_at)
        while not self._closed:
            if self._selection.contested:
                raise OpenCodeError("The native terminal switched conversations during preparation")
            try:
                self._selection.require_selected()
                return self._selection
            except OpenCodeError:
                await asyncio.sleep(0.05)
        raise OpenCodeError("Native observation closed during preparation")

    async def ready(
        self,
        pid: int,
        owns_process: Callable[[], bool],
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        if self._recovering and pid != self._pid:
            raise OpenCodeError("Native recovery found a different terminal process")
        self._pid = pid
        assert self.client is not None and self._server is not None
        self._save_connection(self._server.sockets[0].getsockname()[1], self.client.port, pid)
        async with asyncio.timeout(30):
            while not self._closed:
                if not owns_process():
                    raise OpenCodeError("Native process exited before preparation")
                event = self._latest or {}
                if (
                    event.get("pid") == pid
                    and event.get("ready") is True
                    and time.monotonic() - self._received_at <= 5
                ):
                    break
                await asyncio.sleep(0.05)
            if self._closed:
                raise OpenCodeError("Native process observation closed")
            info = self.database.stat()
            store = json.dumps([str(self.database), info.st_dev, info.st_ino])
            if self._recovering:
                if journal.read(self.history._conn, self.key, self.generation).store != store:
                    raise OpenCodeError("Native conversation store was replaced")
            else:
                journal.begin(
                    self.history._conn,
                    self.key,
                    journal.Launch(
                        self.generation,
                        "local",
                        store,
                        self.directory,
                        pid,
                        prompt=self.prompt,
                        model=self.model,
                    ),
                )
            await prepare(
                self.history._conn,
                self.key,
                self.generation,
                self.client,
                self.selection,
                owns_process,
            )
            self._on_status = on_status
            self._monitor = asyncio.create_task(self._watch(owns_process))

    async def _watch(self, owns_process: Callable[[], bool]) -> None:
        assert self.client is not None
        while not self._closed:
            try:
                revision = time.monotonic_ns()
                state = journal.read(self.history._conn, self.key, self.generation)
                if not owns_process() or self._selection is None:
                    raise OpenCodeError("Native process is unavailable")
                self._selection.require_selected()
                assert state.session_id is not None
                records, status = await asyncio.wait_for(
                    asyncio.to_thread(snapshot, self.client, state.session_id), 30
                )
                journal.read(self.history._conn, self.key, self.generation)
                self._selection.require_selected()
                info = self.database.stat()
                if json.dumps([str(self.database), info.st_dev, info.st_ino]) != state.store:
                    raise OpenCodeError("Native conversation store was replaced")
                source = native_history.NativeSnapshot(
                    str(
                        native_history.snapshot_path(
                            self._root.parent.parent, self.key, state.generation
                        )
                    ),
                    state.generation,
                    state.destination,
                    state.store,
                    state.session_id,
                )
                stamp = int(time.time() * 1000)

                saved = await asyncio.to_thread(
                    native_history.persist, source, records, stamp, revision
                )
                journal.read(self.history._conn, self.key, self.generation)
                self._selection.require_selected()
                if saved:
                    model = next(
                        (
                            str(m["provider"]) + "/" + str(m["model"])
                            for m in reversed(records)
                            if m["role"] == "assistant"
                            and isinstance(m.get("provider"), str)
                            and isinstance(m.get("model"), str)
                        ),
                        None,
                    )
                    control = self.history.restart_control(self.key)
                    control["opencode_observation"] = {
                        "state": status,
                        "observed_at": stamp,
                        "model": model,
                        "revision": revision,
                    }
                    self.history.set_restart_control(self.key, control)
                self._report_status(status)
            except (OpenCodeError, OSError, TimeoutError):
                self._report_status("unknown")
            await asyncio.sleep(2)

    def _report_status(self, state: str) -> None:
        if state != self._status and not self._closed:
            row = self.history.session(self.key)
            control = self.history.restart_control(self.key)
            if (
                row is None
                or row.get("runtime") != "opencode"
                or control.get("opencode_launch", {}).get("generation") != self.generation
            ):
                return
            observation = dict(control.get("opencode_observation") or {})
            observation["state"] = state
            control["opencode_observation"] = observation
            self.history.set_restart_control(self.key, control)
            self._status = state
            if self._on_status:
                self._on_status(state)

    async def close(self) -> None:
        self._closed = True
        if self._monitor is not None:
            self._monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for writer in tuple(self._connections):
            writer.close()
