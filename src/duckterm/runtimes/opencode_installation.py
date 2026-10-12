"""Bounded local installation checks; never start a model or install software."""

from __future__ import annotations

import asyncio
from pathlib import Path

from duckterm.runtimes.opencode_protocol import VERSION, OpenCodeError


async def verify_binary(binary: str, directory: str, env: dict[str, str]) -> None:
    child = await asyncio.create_subprocess_exec(
        binary,
        "--version",
        cwd=directory,
        env=env,
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


async def readiness(env: dict[str, str]) -> str | None:
    if env.get("OPENCODE_TUI_CONFIG"):
        return "Custom OPENCODE_TUI_CONFIG is not supported by this adapter yet."
    if env.get("OPENCODE_DB") == ":memory:":
        return "OpenCode needs a persistent native store for recovery."
    try:
        await verify_binary("opencode", str(Path.home()), env)
    except FileNotFoundError:
        return "Install OpenCode " + VERSION + " on this computer to launch a session."
    except TimeoutError:
        return "The OpenCode version check timed out. Check the installation and retry."
    except OpenCodeError as exc:
        return str(exc)
    except OSError:
        return "Could not run OpenCode on this computer. Check the installation and retry."
    return None
