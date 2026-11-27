"""The default coding harness: which agent CLI DuckTerm starts when nothing
names one, as when Create team spins up a session per role.

Owner, 2026-11-27: a cascade. The setting wins when it is set and installed;
otherwise the one harness installed; otherwise Claude Code among several.
The setting is `<home>/harness-preferences.json`; no schema, no credentials.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from duckterm.core.session_api import APIError
from duckterm.helpers import instance
from duckterm.kits import adapters

FILE = "harness-preferences.json"
HARNESSES = tuple(adapters.ADAPTERS)  # claude-code, codex, copilot, opencode
PREFERRED = "claude-code"


def present() -> list[str]:
    """The coding harnesses installed on this machine, in registry order."""
    return [a.id for a in adapters.ADAPTERS.values() if a.installed_here()]


def command_for(harness: str) -> str:
    return adapters.ADAPTERS[harness].binary


def load() -> dict[str, Any]:
    try:
        data = json.loads((instance.home() / FILE).read_text())
    except FileNotFoundError:
        return {"default_harness": None}
    except (OSError, ValueError) as exc:
        raise APIError(409, "Harness preferences could not be read; save them again") from exc
    value = data.get("default_harness") if isinstance(data, dict) else None
    return {"default_harness": value if value in HARNESSES else None}


def save(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"default_harness"}:
        raise APIError(400, "Expected {default_harness: <harness> | null}")
    chosen = value["default_harness"]
    if chosen is not None and chosen not in HARNESSES:
        raise APIError(400, f"default_harness must be one of {', '.join(HARNESSES)} or null")
    root = instance.home()
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".harness-preferences-", dir=root)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"default_harness": chosen}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return {"default_harness": chosen}


def pick(installed: list[str], default: str | None, asked: str | None = None) -> str | None:
    """The harness a new session gets, or None when none is installed: what
    the caller asked for, else the setting, else the only one, else Claude
    Code among several, else the first installed."""
    for choice in (asked, default):
        if choice in installed:
            return choice
    if not installed:
        return None
    if len(installed) == 1 or PREFERRED in installed:
        return PREFERRED if PREFERRED in installed else installed[0]
    return installed[0]


def describe() -> dict[str, Any]:
    installed = present()
    default = load()["default_harness"]
    return {
        "default_harness": default,
        "present": installed,
        "effective": pick(installed, default),
    }
