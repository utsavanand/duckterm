"""Fixed-action, native-owned harness setup; no owner HTTP or agent-session API.

Invoke with the trusted bundled interpreter: -m duckterm.runtimes.setup
--runtime claude-code|codex --action status|install|login. Optional --executable
is supplied only by a native file-picker selection, never by WebKit input.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
import threading
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from duckterm.runtimes.discovery import parsed_version
from duckterm.runtimes.setup_process import (
    Control,
    Emit,
    ProtocolWriter,
    SetupFailure,
    run_command,
    unique_object,
)

RUNTIMES = {"claude-code": "claude", "codex": "codex"}
ACTIONS = {"status", "install", "login"}
AUTH_TIMEOUT = 15.0
INTERACTIVE_TIMEOUT = 600.0
_INSTALL = {
    "claude-code": "/usr/bin/curl -q --proto '=https' --proto-redir '=https' -fsSL "
    "https://claude.ai/install.sh | /bin/bash",
    "codex": "/usr/bin/curl -q --proto '=https' --proto-redir '=https' -fsSL "
    "https://chatgpt.com/codex/install.sh | /bin/sh",
}
_DROP = {
    "BASH_ENV",
    "ENV",
    "ZDOTDIR",
    "NODE_OPTIONS",
    "NODE_PATH",
    "TMUX",
    "TMUX_PANE",
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_THREAD_ID",
    "UVS_SESSION",
}
_DROP_PREFIX = ("DUCKTERM_", "RUBBERDUCK_", "CODEX_SESSION", "PYTHON", "DYLD_", "LD_")
_INSTALL_ENV = {
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "CURL_CA_BUNDLE",
}


def clean_environment(environment: Mapping[str, str], *, install: bool = False) -> dict[str, str]:
    """Retain actual harness account/profile config, never DuckTerm agent keys."""
    env = {
        key: value
        for key, value in environment.items()
        if key not in _DROP
        and not key.startswith(_DROP_PREFIX)
        and (not install or key in _INSTALL_ENV)
    }
    env.update(DUCKTERM_INTERNAL="1", TERM="xterm-256color")
    home = env.get("HOME", "")
    if not home or not Path(home).is_absolute() or not Path(home).is_dir():
        raise SetupFailure("configuration_unavailable")
    env.setdefault("PATH", os.defpath)
    return env


def executable(
    runtime: str, environment: Mapping[str, str], selected: str | None = None
) -> str | None:
    if runtime not in RUNTIMES:
        raise SetupFailure("invalid_request")
    value = (
        selected
        if selected is not None
        else shutil.which(RUNTIMES[runtime], path=environment.get("PATH", os.defpath))
    )
    if value is None:
        return None
    if not Path(value).is_absolute():
        raise SetupFailure("invalid_executable")
    try:
        path = Path(value).resolve(strict=True)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise SetupFailure("invalid_executable")
    except (OSError, ValueError) as exc:
        raise SetupFailure("invalid_executable") from exc
    return str(path)


@contextlib.contextmanager
def runtime_lock(runtime: str, home: str) -> Iterator[None]:
    """Same OS user/runtime lock across Lite/Code/Full; no credential contents."""
    # Resolve the trusted HOME prefix (macOS /tmp and relocated homes can be
    # symlinks), but never follow an attacker-controlled lock-directory/link.
    root = Path(home).resolve(strict=True) / ".duckterm-runtime-setup"
    with contextlib.suppress(FileExistsError):
        root.mkdir(mode=0o700)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, flags)
    lock = None
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise SetupFailure("configuration_unavailable")
        name = hashlib.sha256(runtime.encode()).hexdigest() + ".lock"
        lock = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        info = os.fstat(lock)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise SetupFailure("configuration_unavailable")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SetupFailure("busy") from exc
        yield
    finally:
        if lock is not None:
            os.close(lock)
        os.close(directory)


def auth_result(runtime: str, code: int, output: bytes) -> dict[str, str]:
    result = {"authentication": "unknown", "credential_method": "unknown"}
    if runtime == "claude-code":
        try:
            value = json.loads(output, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            return result
        if not isinstance(value, dict) or type(value.get("loggedIn")) is not bool:
            return result
        if code == 0 and value["loggedIn"]:
            result["authentication"] = "signed_in"
            method = value.get("authMethod")
            if method in ("claude.ai", "oauth"):
                result["credential_method"] = "account"
            elif method in ("api_key", "api-key"):
                result["credential_method"] = "api_key"
        elif code == 1 and not value["loggedIn"]:
            result["authentication"] = "signed_out"
    elif runtime == "codex":
        # The CLI status command intentionally has no machine-readable flag.
        # Only the fixed prefix selects a method; never return its key/account
        # suffix. Exit 0 reports stored credentials, not quota/model success.
        text = output.decode("utf-8", errors="replace").strip()
        if code == 0:
            result["authentication"] = "signed_in"
            if text == "Logged in using ChatGPT":
                result["credential_method"] = "account"
            elif text.startswith("Logged in using an API key"):
                result["credential_method"] = "api_key"
        elif code == 1 and text == "Not logged in":
            result["authentication"] = "signed_out"
    return result


def probe(
    runtime: str, env: Mapping[str, str], binary: str | None, control: Control
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "installed": binary is not None,
        "version": None,
        "probe": "missing",
        "authentication": "not_checked",
        "credential_method": "unknown",
        "environment_api_key_configured": bool(
            env.get("ANTHROPIC_API_KEY" if runtime == "claude-code" else "OPENAI_API_KEY")
        ),
    }
    if binary is None:
        return result
    with tempfile.TemporaryDirectory(prefix="duckterm-setup-probe-") as directory:
        private = {
            "PATH": env["PATH"],
            "HOME": directory,
            "TMPDIR": directory,
            "TERM": "dumb",
            "NO_COLOR": "1",
            "CI": "1",
        }
        for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            private[key] = directory
        try:
            code, output = run_command(
                [binary, "--version"],
                private,
                directory,
                control=control,
                timeout=4,
                capture_limit=256,
            )
            version = parsed_version(runtime, output) if code == 0 else None
            result.update(version=version, probe="ok" if version else "unrecognized")
        except (SetupFailure, OSError) as exc:
            if str(exc) in (
                "cancelled",
                "invalid_command",
                "cleanup_failed",
                "transport_unavailable",
            ):
                raise
            result["probe"] = "timeout" if str(exc) == "timeout" else "failed"
        # Version output is not an authentication gate. A newer CLI can still
        # implement the fixed status command even when its version is unknown.
        args = (
            [binary, "auth", "status"] if runtime == "claude-code" else [binary, "login", "status"]
        )
        try:
            code, output = run_command(args, env, directory, control=control, timeout=AUTH_TIMEOUT)
            result.update(auth_result(runtime, code, output))
        except (SetupFailure, OSError) as exc:
            if str(exc) in (
                "cancelled",
                "invalid_command",
                "cleanup_failed",
                "transport_unavailable",
            ):
                raise
            result.update(authentication="unknown", reason="auth_status_unavailable")
    return result


def execute(
    runtime: str,
    action: str,
    *,
    environment: Mapping[str, str],
    emit: Emit,
    control: Control,
    selected: str | None = None,
) -> None:
    if (
        runtime not in RUNTIMES
        or action not in ACTIONS
        or (action == "install" and selected is not None)
    ):
        raise SetupFailure("invalid_request")
    env = clean_environment(environment, install=action == "install")
    with runtime_lock(runtime, env["HOME"]):
        binary = None if action == "install" else executable(runtime, env, selected)
        if action == "status":
            emit({"type": "status", "runtime": runtime, **probe(runtime, env, binary, control)})
            return
        if action == "login" and binary is None:
            raise SetupFailure("not_installed")
        argv = (
            ["/bin/bash", "--noprofile", "--norc", "-o", "pipefail", "-c", _INSTALL[runtime]]
            if action == "install"
            else (
                [str(binary), "auth", "login"]
                if runtime == "claude-code"
                else [str(binary), "login"]
            )
        )
        with tempfile.TemporaryDirectory(prefix="duckterm-setup-action-") as directory:
            emit({"type": "ready", "runtime": runtime, "action": action})
            code, _ = run_command(
                argv,
                env,
                directory,
                control=control,
                timeout=INTERACTIVE_TIMEOUT,
                interactive=True,
                emit=emit,
            )
            emit({"type": "exited", "exit_code": code, "cancelled": False})


def main(arguments: Sequence[str] | None = None) -> int:
    writer = ProtocolWriter(sys.stdout.fileno())
    args = list(sys.argv[1:] if arguments is None else arguments)
    control = Control(sys.stdin.fileno(), threading.Event())
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        # Do not let argparse echo arbitrary caller values/paths to stderr.
        if (
            len(args) not in (4, 6)
            or args[0] != "--runtime"
            or args[2] != "--action"
            or (len(args) == 6 and args[4] != "--executable")
        ):
            raise SetupFailure("invalid_request")
        for sig in previous:
            signal.signal(sig, lambda _sig, _frame: control.cancelled.set())
        execute(
            args[1],
            args[3],
            environment=os.environ,
            emit=writer,
            control=control,
            selected=args[5] if len(args) == 6 else None,
        )
        return 0
    except SetupFailure as exc:
        allowed = {
            "invalid_request",
            "invalid_executable",
            "configuration_unavailable",
            "busy",
            "not_installed",
            "invalid_command",
            "cancelled",
            "timeout",
            "output_limit",
            "cleanup_failed",
            "transport_unavailable",
            "setup_unavailable",
        }
        reason = str(exc) if str(exc) in allowed else "setup_unavailable"
        with contextlib.suppress(SetupFailure):
            writer({"type": "error", "reason": reason})
        return 1
    except (OSError, ValueError, RuntimeError):
        with contextlib.suppress(SetupFailure):
            writer({"type": "error", "reason": "setup_unavailable"})
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
