"""Owner-only sibling shells. Never register an agent supervisor or capture pipe."""

from __future__ import annotations

import asyncio
import os
import pwd
import re
import shlex
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.agents import tmux, tmux_stream
from duckterm.core.session_api import APIError

if TYPE_CHECKING:
    from duckterm.server import Server


def target_for(key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key) or key in {".", ".."} or key.endswith("-sh"):
        raise APIError(400, "Invalid or reserved session key")
    return tmux.target_for(key) + "-sh"


def _missing(error: str) -> bool:
    return any(
        text in error
        for text in (
            "can't find session",
            "can't find pane",
            "no server running",
            "no current target",
            "No such file or directory",
        )
    )


def inspect_shell(key: str) -> dict[str, Any]:
    target = target_for(key)
    ok, error = tmux._tmux("has-session", "-t", "=" + target)
    if not ok:
        if _missing(error):
            return {"open": False, "confirmation_required": False, "foreground": None}
        raise APIError(503, "Shell host unavailable: " + error.strip())
    ok, result = tmux._tmux(
        "display-message",
        "-p",
        "-t",
        "=" + target + ":",
        "#{pane_id}\t#{pane_pid}\t#{pane_tty}\t#{pane_current_command}\t#{@duckterm_owner_shell}",
    )
    if not ok:
        if _missing(result):
            return {"open": False, "confirmation_required": False, "foreground": None}
        raise APIError(503, "Shell host unavailable: " + result.strip())
    fields = result.strip().split("\t")
    if len(fields) != 5 or fields[4] != key:
        raise APIError(409, "The shell target is occupied by an unrecognized terminal")
    pane, pid, _tty, command, _ = fields
    if not re.fullmatch(r"%\d+", pane):
        raise APIError(503, "Cannot determine shell pane identity")
    ok, shell = tmux._tmux("show-option", "-qv", "-t", target, "@duckterm_owner_shell_binary")
    foreground_group = None
    busy = True  # unknown foreground state requires confirmation
    if ok:
        try:
            # macOS tcgetpgrp rejects a tty that is not our controlling tty.
            # ps exposes the shell and foreground groups on both Darwin/Linux.
            groups = subprocess.run(
                ["ps", "-o", "pgid=", "-o", "tpgid=", "-p", pid],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            ).stdout.split()
            shell_group, foreground_group = map(int, groups)
            shell_name = Path(shell.strip()).name
            names = {shell_name, "-" + shell_name}
            if shell_name == "sh":
                names.update({"bash", "dash", "ksh"})
            busy = foreground_group <= 0 or foreground_group != shell_group or command not in names
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return {
        "open": True,
        "pane_id": pane,
        "foreground": command or "unknown process",
        "confirmation_required": busy,
        "confirmation_token": f"{pane}:{foreground_group}:{command}",
    }


def _spawn(key: str, cwd: str) -> None:
    shell = os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
    if not os.path.isabs(shell) or not os.access(shell, os.X_OK):
        raise APIError(503, "The owner's configured shell is unavailable")
    # The helper runs inside tmux, after tmux's global environment inheritance.
    command = shlex.join([sys.executable, "-m", "duckterm.helpers.owner_shell", shell])
    # Publish ownership in the creation command queue, before discovery can
    # mistake this sibling for an agent. A suffix alone is not ownership.
    tmux.spawn(
        key + "-sh",
        command,
        cwd,
        session_options={"@duckterm_owner_shell": key, "@duckterm_owner_shell_binary": shell},
    )


class ShellTerminal:
    def __init__(self, pane: str) -> None:
        self.pane = pane

    def subscribe_bytes(self) -> AsyncGenerator[bytes, None]:
        return tmux_stream.stream(self.pane)

    async def write(self, data: bytes) -> None:
        if len(data) > 64 * 1024:
            raise ValueError("Shell input frame exceeds 64 KiB")
        if not await asyncio.to_thread(tmux.send_raw, self.pane, data):
            raise ValueError("Shell is closed")

    def resize(self, cols: int, rows: int) -> bool:
        return tmux.resize_window(self.pane, min(500, max(1, cols)), min(300, max(1, rows)))


class SessionShells:
    def __init__(self, server: Server) -> None:
        self.server = server
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def eligible(self, key: str) -> dict[str, Any]:
        target_for(key)
        row = self.server.history.session(key)
        if row is None:
            raise APIError(404, "Session is unavailable on this host")
        if not row.get("launched") or row.get("heartbeat"):
            raise APIError(409, "Shells require a DuckTerm-owned terminal")
        if row.get("state") in {"archived", "merged"} or self.server.archives.pending(key):
            raise APIError(409, "Session is archived, merged or awaiting archive")
        return row

    async def status(self, key: str) -> dict[str, Any]:
        self.eligible(key)
        if not await asyncio.to_thread(tmux.has_tmux):
            raise APIError(503, "Shell unavailable: tmux is not installed on this host")
        return await asyncio.to_thread(inspect_shell, key)

    async def open(self, key: str) -> dict[str, Any]:
        async with self.lock(key):
            row = self.eligible(key)
            status = await self.status(key)
            if status["open"]:
                return status
            cwd = str(row.get("worktree_path") or row.get("cwd") or "")
            if not cwd or not await asyncio.to_thread(Path(cwd).is_dir):
                raise APIError(409, "The session's project folder is unavailable")
            self.eligible(key)
            # Finish a spawn even if the requesting HTTP task is cancelled.
            # Archive/delete share this lock, so they cannot miss an in-flight shell.
            task = asyncio.create_task(asyncio.to_thread(_spawn, key, cwd))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            return await asyncio.to_thread(inspect_shell, key)

    async def close(
        self, key: str, *, force: bool = False, expected: str | None = None
    ) -> dict[str, Any]:
        async with self.lock(key):
            self.eligible(key)
            status = await asyncio.to_thread(inspect_shell, key)
            if (
                status["open"]
                and status["confirmation_required"]
                and (not force or expected != status["confirmation_token"])
            ):
                return {**status, "closed": False, "confirmation_required": True}
            await self.destroy(key)
            return {"open": False, "closed": True, "confirmation_required": False}

    async def destroy(self, key: str) -> None:
        """Caller holds the lifecycle lock; only archive/delete bypass confirmation."""
        try:
            target_for(key)
        except APIError:
            # Keys predating this API may not qualify for a sibling shell.
            # They still need ordinary agent archive/delete cleanup.
            return
        if not await asyncio.to_thread(tmux.has_tmux):
            return
        try:
            status = await asyncio.to_thread(inspect_shell, key)
        except APIError as exc:
            if exc.status == 409:
                # A legacy agent can occupy the derived name. Shell cleanup
                # must neither kill it nor prevent the parent agent's deletion.
                return
            raise
        if status["open"] and not await asyncio.to_thread(tmux.kill_session, "=" + target_for(key)):
            raise APIError(503, "Could not close the session shell")

    async def terminal(self, key: str) -> ShellTerminal:
        async with self.lock(key):
            status = await self.status(key)
            if not status["open"]:
                raise APIError(404, "Shell is closed; open it before attaching")
            return ShellTerminal(status["pane_id"])
