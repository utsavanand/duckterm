"""Profile-bound MCP runtime discovery without a shared Python/PATH dependency."""

import hashlib
import json
import os
import plistlib
import re
import shutil
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any, TypeGuard

from duckterm.agents.hook_files import _lock, _read, _write
from duckterm.helpers import instance

REVISION = 1
CONTEXT = (
    "DUCKTERM_HOME",
    "DUCKTERM_INSTANCE",
    "DUCKTERM_DESKTOP_PROFILE",
    "DUCKTERM_PORT",
    "DUCKTERM_TMUX_SOCKET",
    "DUCKTERM_URL",
)


def launcher_path() -> Path:
    return Path.home().resolve() / ".duckterm-connectors/v1/duckterm-connector.sh"


def legacy_context() -> dict[str, str]:
    return {
        "DUCKTERM_HOME": str((Path.home() / ".duckterm").resolve()),
        "DUCKTERM_INSTANCE": "",
        "DUCKTERM_DESKTOP_PROFILE": "",
        "DUCKTERM_PORT": "4300",
        "DUCKTERM_TMUX_SOCKET": "duckterm",
        "DUCKTERM_URL": "http://127.0.0.1:4300",
    }


def is_legacy_full() -> bool:
    return (
        instance.instance_id() is None
        and instance.home().resolve() == (Path.home() / ".duckterm").resolve()
    )


def runtime_path(context: dict[str, str]) -> Path:
    identity = json.dumps([context["DUCKTERM_HOME"], context["DUCKTERM_INSTANCE"]])
    return (
        launcher_path().parent
        / "runtimes"
        / (hashlib.sha256(identity.encode()).hexdigest() + ".target")
    )


def _ensure_launcher() -> None:
    path = launcher_path()
    source = Path(str(files("duckterm").joinpath("hooks/duckterm-connector.sh"))).read_bytes()
    with _lock(path.parent / "assets.json", create=True) as directory:
        assert directory is not None
        old = _read(path.name, directory)
        raw = _read("assets.json", directory)
        if raw is not None:
            receipt = json.loads(raw)
            if (
                not isinstance(receipt, dict)
                or receipt.get("format") != 1
                or type(receipt.get("revision")) is not int
                or receipt["revision"] < 1
                or old is None
                or receipt.get("sha256") != hashlib.sha256(old).hexdigest()
            ):
                raise ValueError("Shared connector launcher is modified; leaving it unchanged")
            if receipt["revision"] > REVISION:
                return
            if receipt["revision"] == REVISION and old != source:
                raise ValueError("Connector launcher revision must increase before updating")
        elif old is not None:
            raise ValueError("Shared connector launcher has no ownership receipt")
        if old == source:
            return
        _write(path.name, source, directory, mode=0o700)
        receipt = {"format": 1, "revision": REVISION, "sha256": hashlib.sha256(source).hexdigest()}
        _write("assets.json", (json.dumps(receipt) + "\n").encode(), directory)


def register_runtime(
    command: str, *, context: dict[str, str] | None = None, replace: bool = True
) -> Path:
    """Record non-secret target context and its exact executable; never use another profile."""
    if context is None:
        profile = instance.configured_profile()
        if profile is not None:
            instance.validate_desktop_context(profile["edition"])
        context = instance.launch_env()
    if not Path(command).is_absolute():
        resolved = shutil.which(command)
        if resolved is None:
            raise ValueError("Duckterm CLI is unavailable; open this edition's app and retry")
        command = resolved
    # A global shim may later be repointed while another edition is installed.
    # Pin its current target so that cannot silently change this profile's CLI.
    command = str(Path(command).resolve())
    match = re.fullmatch(r"(/.+\.app)/Contents/Resources/bin/duckterm", command)
    if match:
        plist = Path(match[1]) / "Contents/Info.plist"
        if plist.is_file():
            metadata = plistlib.loads(plist.read_bytes())
            compiled = metadata.get("DucktermProfile")
            expected = context["DUCKTERM_DESKTOP_PROFILE"] or context["DUCKTERM_INSTANCE"] or "full"
            if compiled and compiled != expected:
                raise ValueError("Connector CLI belongs to another desktop profile")
    values = [
        "duckterm-connector-target-v1",
        *(context[key] for key in CONTEXT),
        command,
        os.environ.get("PATH", os.defpath),
    ]
    if any("\n" in value or "\r" in value or "\0" in value for value in values):
        raise ValueError("Connector runtime paths cannot contain line breaks")
    _ensure_launcher()
    path = runtime_path(context)
    with _lock(path, create=True) as directory:
        assert directory is not None
        old = _read(path.name, directory)
        if old is not None and not replace:
            return launcher_path()
        _write(path.name, ("\n".join(values) + "\n").encode(), directory)
    return launcher_path()


def owned_entry(entry: Any, name: str) -> TypeGuard[dict[str, Any]]:
    """Recognize exact managed argv, never a name alone or a shell wrapper."""
    if (
        not isinstance(entry, dict)
        or entry.get("args") != ["connector-run", name]
        or entry.get("env") not in (None, {})
        or not isinstance(entry.get("command"), str)
    ):
        return False
    command = entry["command"]
    if command == str(launcher_path()) or command == "duckterm":
        return True
    path = Path(command)
    if not path.is_absolute() or ".." in path.parts:
        return False
    if path in {Path.home() / ".local/bin/duckterm", Path(sys.executable).with_name("duckterm")}:
        return True
    roots = [Path.home() / ".local/pipx", Path.home() / ".local/share/pipx"]
    if os.environ.get("PIPX_HOME"):
        roots.append(Path(os.environ["PIPX_HOME"]))
    return any(path == root / "venvs/duckterm/bin/duckterm" for root in roots) or bool(
        re.fullmatch(r"/.+\.app/Contents/Resources/bin/duckterm", command)
    )


def preserve_legacy_runtime(entry: dict[str, Any]) -> None:
    """Migrating Code must not redirect existing unscoped Full callers into Code."""
    if entry["command"] == str(launcher_path()) or runtime_path(legacy_context()).is_file():
        return
    command = entry["command"]
    if command == "duckterm":
        # Do not resolve against Code's app-local PATH. The legacy global shim is
        # the only unambiguous relative registration we can migrate from Code.
        command = str(Path.home() / ".local/bin/duckterm")
        if not Path(command).is_file():
            raise ValueError("Open DuckTerm Full once to migrate its legacy connector runtime")
    register_runtime(command, context=legacy_context(), replace=False)
