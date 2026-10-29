"""Shared, versioned hook assets and profile ownership, independent of app paths.

The v1 wire contract is the existing event JSON and explicit DUCKTERM_* target
environment. Unscoped agents retain the legacy Full target. Registering a
profile only keeps the shared entry alive; it never changes that default.
"""

import contextlib
import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from duckterm.agents.hook_files import _lock, _read, _write
from duckterm.helpers import instance

# Increment when changing the shared script or its pinned jq. An older app may
# use a newer v1 handler, but must never replace it with an older handler.
REVISION = 2
LEGACY_FULL = "legacy-full"


def shared_script_path() -> Path:
    return Path.home().resolve() / ".duckterm-hooks/v1/duckterm-hook.sh"


def _config_path(path: Path) -> Path:
    absolute = path.absolute()
    home = Path.home().absolute()
    return (
        home.resolve() / absolute.relative_to(home) if absolute.is_relative_to(home) else absolute
    )


def profile_key() -> str:
    home = instance.home().resolve()
    if instance.instance_id() is None and home == (Path.home() / ".duckterm").resolve():
        return LEGACY_FULL
    identity = json.dumps([str(home), instance.instance_id()], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def _asset(name: str, directory: int) -> bytes | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("Unsafe shared hook asset") from exc
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
            raise ValueError("Invalid shared hook asset")
        return stream.read()


def _executable(name: str, content: bytes, directory: int) -> None:
    _write(name, content, directory, mode=0o700)


def _bundled_jq(source: Path) -> Path | None:
    match = re.fullmatch(
        r"(/.+\.app/Contents/Resources)/python/lib/python[0-9]+\.[0-9]+"
        r"/site-packages/duckterm/hooks/duckterm-hook\.sh",
        str(source),
    )
    if match:
        jq = Path(match[1]) / "jq/bin/jq"
        if not jq.is_file() or not os.access(jq, os.X_OK):
            raise ValueError("Bundled jq is missing; shared hooks were not changed")
        return jq
    named = os.environ.get("DUCKTERM_BUNDLED_JQ")
    if named and os.access(named, os.X_OK):
        return Path(named)
    # Do not copy a system jq: it may depend on libraries outside the binary.
    # CLI-only installations retain the script's existing PATH fallback.
    return None


def _notices(jq: Path | None) -> bytes | None:
    if jq is None:
        return None
    root = jq.parent.parent
    pieces = []
    for path in (
        root / "licenses/jq-COPYING",
        root / "licenses/oniguruma-COPYING",
        root.parent / "LICENSE-duckterm",
    ):
        if path.is_file():
            pieces.append(path.name.encode() + b"\n\n" + path.read_bytes())
    return b"\n\n".join(pieces) if pieces else None


def ensure_shared_script(source: Path) -> Path:
    """Copy self-contained assets atomically, refusing modified or newer assets.

    The receipt makes a crash or a user edit fail closed on the next upgrade.
    App installs always supply jq. CLI-only installs retain their historic
    optional-jq behavior when neither a bundled nor a PATH jq is available.
    """
    destination = shared_script_path()
    with _lock(destination.parent / "assets.json", create=True) as directory:
        assert directory is not None
        raw = _read("assets.json", directory)
        old_script, old_jq = _asset(destination.name, directory), _asset("jq", directory)
        old_notices = _asset("NOTICES.txt", directory)
        if raw is not None:
            record = json.loads(raw)
            if (
                not isinstance(record, dict)
                or record.get("format") != 1
                or type(record.get("revision")) is not int
                or record["revision"] < 1
                or old_script is None
                or record.get("script_sha256") != hashlib.sha256(old_script).hexdigest()
                or record.get("jq_sha256")
                != (hashlib.sha256(old_jq).hexdigest() if old_jq is not None else None)
                or record.get("notices_sha256")
                != (hashlib.sha256(old_notices).hexdigest() if old_notices is not None else None)
            ):
                raise ValueError("Shared hook assets are modified; leaving them unchanged")
            if record["revision"] > REVISION:
                return destination
        elif old_script is not None or old_jq is not None or old_notices is not None:
            raise ValueError("Shared hook assets have no ownership receipt")
        script = source.read_bytes()
        jq_path = _bundled_jq(source)
        jq = jq_path.read_bytes() if jq_path else old_jq
        notices = _notices(jq_path) or old_notices
        if raw is not None and record["revision"] == REVISION and old_script != script:
            raise ValueError("Shared hook revision changed without a revision increment")
        # Keep an existing same-revision jq: another edition may have supplied it
        # without any PATH dependency. Never downgrade to a system jq later.
        if raw is not None and record["revision"] == REVISION and old_jq is not None:
            jq = old_jq
            notices = old_notices
        if old_script == script and old_jq == jq and old_notices == notices:
            return destination
        _executable(destination.name, script, directory)
        if jq is not None:
            _executable("jq", jq, directory)
        if notices is not None:
            _write("NOTICES.txt", notices, directory)
        receipt = {
            "format": 1,
            "revision": REVISION,
            "script_sha256": hashlib.sha256(script).hexdigest(),
            "jq_sha256": hashlib.sha256(jq).hexdigest() if jq is not None else None,
            "notices_sha256": hashlib.sha256(notices).hexdigest() if notices is not None else None,
        }
        _write("assets.json", (json.dumps(receipt) + "\n").encode(), directory)
    return destination


class Registrations:
    def __init__(self, directory: int | None) -> None:
        self.directory = directory
        raw = _read("registrations.json", directory) if directory is not None else None
        data = json.loads(raw) if raw is not None else {"format": 1, "entries": {}}
        if (
            not isinstance(data, dict)
            or data.get("format") != 1
            or not isinstance(data.get("entries"), dict)
        ):
            raise ValueError("Invalid shared hook registrations")
        self.entries: dict[str, Any] = data["entries"]
        for entry in self.entries.values():
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("path"), str)
                or not isinstance(entry.get("runtime"), str)
                or not isinstance(entry.get("profiles"), list)
                or not all(isinstance(p, str) for p in entry["profiles"])
            ):
                raise ValueError("Invalid shared hook registration")

    @staticmethod
    def _key(path: Path, runtime: str) -> str:
        value = json.dumps([str(_config_path(path)), runtime])
        return hashlib.sha256(value.encode()).hexdigest()

    def members(self, path: Path, runtime: str, *, legacy: bool) -> set[str]:
        entry = self.entries.get(self._key(path, runtime))
        if entry is not None:
            return set(entry["profiles"])
        return {LEGACY_FULL} if legacy else set()

    def save(self, path: Path, runtime: str, members: set[str]) -> None:
        if self.directory is None:
            return
        key = self._key(path, runtime)
        if members:
            self.entries[key] = {
                "path": str(_config_path(path)),
                "runtime": runtime,
                "profiles": sorted(members),
            }
        else:
            self.entries.pop(key, None)
        data = {"format": 1, "entries": self.entries}
        _write("registrations.json", (json.dumps(data, indent=2) + "\n").encode(), self.directory)


@contextlib.contextmanager
def registrations(*, create: bool) -> Iterator[Registrations]:
    # Always acquire this before a config/file-drop lock, so parallel apps can't
    # lose each other's memberships or deadlock by reversing the lock order.
    with _lock(shared_script_path().parent / "registrations.json", create=create) as directory:
        yield Registrations(directory)


def registered_profiles(path: Path, runtime: str, *, legacy: bool = True) -> set[str]:
    """Read-only inventory for uninstall planning; creates no files or locks."""
    file = shared_script_path().parent / "registrations.json"
    if not file.exists():
        return {LEGACY_FULL} if legacy else set()
    # Inventory is advisory. Mutations re-read under the descriptor-based lock.
    data = json.loads(file.read_text())
    entry = data["entries"].get(Registrations._key(path, runtime))
    return set(entry["profiles"]) if entry else ({LEGACY_FULL} if legacy else set())
