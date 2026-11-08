"""Read-only installation discovery, never a login or model readiness check."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, TypedDict

from duckterm.harnesses import REGISTRY
from duckterm.runtimes.opencode_protocol import VERSION as OPENCODE_VERSION

PROBE_TIMEOUT = 4.0
CLEANUP_TIMEOUT = 1.0
MAX_OUTPUT = 256
PARALLEL_PROBES = 2
PROBE_QUEUE_TIMEOUT = 0.25
# Shared across requests and event loops. A worker releases its own slot only
# after child reaping and temporary-directory cleanup, including cancellation.
_PROBE_SLOTS = threading.BoundedSemaphore(PARALLEL_PROBES)
_RUNTIMES = (
    ("claude-code", "Claude Code", "claude"),
    ("codex", "Codex", "codex"),
    ("copilot", "GitHub Copilot", "copilot"),
    ("opencode", "OpenCode", "opencode"),
)
_VERSION = r"([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-(?:alpha|beta|rc)(?:\.[0-9]{1,4})?)?)"
_PREFIXES = {
    "claude-code": r"(?:(?:claude|claude-code|Claude Code)\s+)?",
    "codex": r"(?:codex(?:-cli)?\s+)?",
    "copilot": r"(?:(?:GitHub Copilot(?: CLI)?|copilot(?:-cli)?)\s+)?",
    "opencode": "",
}


class RuntimeStatus(TypedDict):
    id: str
    label: str
    command: str
    installed: bool
    version: str | None
    probe: Literal["ok", "missing", "timeout", "failed", "unrecognized"]
    support: Literal["available", "unavailable", "unknown"]
    support_reason: str | None
    authentication: Literal["not_checked"]


class Discovery(TypedDict):
    version: int
    runtimes: list[RuntimeStatus]


def parsed_version(runtime: str, output: bytes) -> str | None:
    """Return only a short version token, never arbitrary CLI output or paths."""
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeError:
        return None
    if not lines:
        return None
    if runtime == "opencode" and len(lines) != 1:
        return None  # Match the existing adapter's exact version-output check.
    suffix = {"claude-code": r"(?: \(Claude Code\))?", "copilot": r"\.?"}.get(runtime, "")
    match = re.fullmatch(_PREFIXES[runtime] + _VERSION + suffix, lines[0].strip())
    return match[1] if match else None


def _version_output(binary: str, launch_path: str, cancelled: threading.Event) -> bytes:
    # A version command must not inherit agent keys, hook configuration, Python
    # injection, project cwd, or the user's default CLI configuration directory.
    with tempfile.TemporaryDirectory(prefix="duckterm-runtime-probe-") as directory:
        env = {
            "PATH": launch_path,
            "HOME": directory,
            "XDG_CONFIG_HOME": directory,
            "XDG_CACHE_HOME": directory,
            "XDG_DATA_HOME": directory,
            "XDG_STATE_HOME": directory,
            "TMPDIR": directory,
            "TERM": "dumb",
            "NO_COLOR": "1",
            "CI": "1",
        }
        child: subprocess.Popen[bytes] | None = None
        try:
            if cancelled.is_set():
                raise RuntimeError("probe_cancelled")
            deadline = time.monotonic() + PROBE_TIMEOUT
            child = subprocess.Popen(
                [binary, "--version"],
                cwd=directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            assert child.stdout is not None
            output = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    if cancelled.is_set():
                        raise RuntimeError("probe_cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("probe_timeout")
                    if not selector.select(min(remaining, 0.1)):
                        continue
                    block = os.read(child.stdout.fileno(), MAX_OUTPUT + 1 - len(output))
                    if not block:
                        break
                    output.extend(block)
                    if len(output) > MAX_OUTPUT:
                        raise ValueError("version_output_limit")
            # Keep the leader unreaped during pipe reads. On timeout/cancel a
            # wrapper and any child retaining its pipe can be stopped together;
            # its process-group ID cannot have been reused before this wait.
            try:
                code = child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError("probe_timeout") from exc
            if code:
                raise RuntimeError("version_exit_status")
            return bytes(output)
        finally:
            if child is not None:
                try:
                    if child.returncode is None:
                        signal_error: OSError | None = None
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        except OSError as exc:
                            # Darwin can deny a group signal after its last live
                            # member exits. Still reap our leader; a live leader
                            # can be stopped by its exact, still-owned PID.
                            signal_error = exc
                            with contextlib.suppress(OSError):
                                child.kill()
                        finally:
                            try:
                                child.wait(timeout=CLEANUP_TIMEOUT)
                            except subprocess.TimeoutExpired as exc:
                                raise TimeoutError("probe_cleanup_timeout") from exc
                        if signal_error is not None:
                            # Reaping alone cannot prove descendants were
                            # stopped when the group signal was denied.
                            raise signal_error
                finally:
                    if child.stdout is not None:
                        child.stdout.close()


async def version_output(binary: str, launch_path: str) -> bytes:
    deadline = time.monotonic() + PROBE_QUEUE_TIMEOUT
    while not _PROBE_SLOTS.acquire(blocking=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("probe_queue_timeout")
        await asyncio.sleep(min(remaining, 0.02))

    cancelled = threading.Event()
    loop = asyncio.get_running_loop()
    work: asyncio.Future[bytes] = loop.create_future()

    def publish(output: bytes, error: Exception | None) -> None:
        if not work.done():
            if error is None:
                work.set_result(output)
            else:
                work.set_exception(error)

    def probe() -> None:
        output = b""
        error: Exception | None = None
        try:
            output = _version_output(binary, launch_path, cancelled)
        except BaseException as exc:
            error = exc if isinstance(exc, Exception) else RuntimeError("probe_failed")
        finally:
            _PROBE_SLOTS.release()
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(publish, output, error)

    try:
        # Admission bounds subprocess work. Unlike a queued to_thread task,
        # cancellation cannot prevent a reserved worker from ever starting or
        # release its slot before its subprocess actually finishes cleanup.
        threading.Thread(target=probe, name="duckterm-runtime-probe", daemon=True).start()
    except BaseException:
        _PROBE_SLOTS.release()
        raise
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        cancelled.set()
        # The worker checks cancellation at least every 100ms while reading and
        # owns process reaping and temporary-directory cleanup before returning.
        # Repeated cancellation must not leave cleanup running unobserved.
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not work.cancelled():
            work.exception()
        raise


async def inspect_runtime(
    runtime: str, label: str, command: str, environment: Mapping[str, str]
) -> RuntimeStatus:
    result: RuntimeStatus = {
        "id": runtime,
        "label": label,
        "command": command,
        "installed": False,
        "version": None,
        "probe": "missing",
        "support": "unavailable",
        "support_reason": "not_installed",
        "authentication": "not_checked",
    }
    launch_path = os.pathsep.join(
        entry
        for entry in environment.get("PATH", os.defpath).split(os.pathsep)
        if os.path.isabs(entry)
    )
    if not launch_path:
        return result
    try:
        binary = await asyncio.to_thread(shutil.which, command, path=launch_path)
    except OSError:
        result.update(
            {"probe": "failed", "support": "unknown", "support_reason": "discovery_failed"}
        )
        return result
    if binary is None:
        return result
    result["installed"] = True
    try:
        output = await version_output(str(Path(binary).absolute()), launch_path)
    except TimeoutError:
        result.update({"probe": "timeout", "support": "unknown", "support_reason": "probe_timeout"})
        return result
    except (OSError, RuntimeError):
        result.update({"probe": "failed", "support": "unknown", "support_reason": "probe_failed"})
        return result
    except ValueError:
        result.update(
            {
                "probe": "unrecognized",
                "support": "unknown",
                "support_reason": "version_unrecognized",
            }
        )
        return result
    version = parsed_version(runtime, output)
    result["version"] = version
    if version is None:
        result.update(
            {
                "probe": "unrecognized",
                "support": "unknown",
                "support_reason": "version_unrecognized",
            }
        )
        return result
    result.update({"probe": "ok", "support": "available", "support_reason": None})
    if runtime not in REGISTRY:
        result.update({"support": "unavailable", "support_reason": "adapter_unavailable"})
    elif runtime == "opencode":
        if version != OPENCODE_VERSION:
            result.update({"support": "unavailable", "support_reason": "unsupported_version"})
        elif environment.get("OPENCODE_TUI_CONFIG") or environment.get("OPENCODE_DB") == ":memory:":
            result.update({"support": "unavailable", "support_reason": "unsupported_configuration"})
    # "available" describes the registered launch adapter, not authentication,
    # model access, or every feature (e.g. Codex's separate harness-switch fence).
    return result


async def discover(environment: Mapping[str, str] | None = None) -> Discovery:
    # Finder launches already receive ServerEnvironment.make's augmented PATH.
    # Use that same backend PATH; never invoke a login shell to reconstruct it.
    env = dict(os.environ if environment is None else environment)
    semaphore = asyncio.Semaphore(PARALLEL_PROBES)

    async def limited(spec: tuple[str, str, str]) -> RuntimeStatus:
        async with semaphore:
            return await inspect_runtime(*spec, env)

    rows = await asyncio.gather(*(limited(spec) for spec in _RUNTIMES))
    return {"version": 1, "runtimes": rows}
