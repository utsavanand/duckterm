"""Guarded file-drop hooks: a content marker alone never proves ownership."""

import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path

MARKER = b"// DuckTerm managed plugin v1\n"
LIMIT = 1024 * 1024


def _read(name: str, directory: int) -> bytes | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("Refusing unsafe plugin path") from exc
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Plugin and ownership receipt must be regular files")
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("Plugin file exceeds the managed size limit")
    return data


def _write(name: str, data: bytes, directory: int, *, mode: int = 0o600) -> None:
    temporary = ".duckterm-" + secrets.token_hex(16)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
    try:
        # Set the final mode before publishing: executable hook assets must not
        # briefly become non-executable between replacement and chmod.
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory)


@contextlib.contextmanager
def _lock(path: Path, *, create: bool) -> Iterator[int | None]:
    # A relocated home (or /tmp-backed fixture) can have symlinked ancestors.
    # Resolve that trusted prefix only; configuration directories below it
    # must still pass the descriptor-based NOFOLLOW walk.
    absolute = path.absolute()
    home = Path.home().absolute()
    if absolute.is_relative_to(home):
        absolute = home.resolve() / absolute.relative_to(home)
    # A replaced configuration parent can never redirect reads, temporary
    # files, writes or unlink to another tree.
    directory = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    lock = None
    try:
        for part in absolute.parent.parts[1:]:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o700, dir_fd=directory)
            try:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
            except FileNotFoundError:
                yield None
                return
            except OSError as exc:
                raise ValueError("Refusing unsafe plugin directory") from exc
            os.close(directory)
            directory = child
        try:
            name = "." + path.name + ".duckterm-lock"
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                lock = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            except FileExistsError:
                # Separate creation from opening. Concurrent O_CREAT opens on
                # macOS can fail with ENOENT while another process creates the
                # same file; an exclusive create has an unambiguous loser.
                lock = os.open(name, flags, dir_fd=directory)
        except OSError as exc:
            raise ValueError("Refusing unsafe plugin lock") from exc
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            raise ValueError("Plugin lock must be a regular file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory
    finally:
        if lock is not None:
            os.close(lock)
        os.close(directory)


def _owned(name: str, receipt: str, directory: int) -> bytes | None:
    content, saved = _read(name, directory), _read(receipt, directory)
    if content is None:
        if saved is not None:
            raise ValueError("Plugin is missing but an ownership receipt remains; inspect it first")
        return None
    try:
        info = json.loads(saved) if saved is not None else {}
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid plugin ownership receipt") from exc
    if (
        not isinstance(info, dict)
        or info.get("format") != 1
        or info.get("name") != name
        or not content.startswith(MARKER)
        or info.get("sha256") != hashlib.sha256(content).hexdigest()
    ):
        raise ValueError("Existing plugin is unowned or modified; leaving it unchanged")
    return content


def install_file(path: Path, payload: bytes) -> None:
    if not payload.startswith(MARKER) or len(payload) > LIMIT:
        raise ValueError("Invalid managed plugin payload")
    with _lock(path, create=True) as directory:
        if directory is None:
            raise ValueError("Plugin directory disappeared during installation")
        receipt = "." + path.name + ".duckterm-owned.json"
        if _owned(path.name, receipt, directory) == payload:
            return
        record = {"format": 1, "name": path.name, "sha256": hashlib.sha256(payload).hexdigest()}
        # A crash between these writes leaves an unowned/mismatching pair that
        # fails closed, rather than authorizing a later overwrite by its marker.
        _write(path.name, payload, directory)
        _write(receipt, (json.dumps(record) + "\n").encode(), directory)


def uninstall_file(path: Path) -> None:
    with _lock(path, create=False) as directory:
        if directory is None:
            return
        receipt = "." + path.name + ".duckterm-owned.json"
        if _owned(path.name, receipt, directory) is not None:
            os.unlink(path.name, dir_fd=directory)
            os.unlink(receipt, dir_fd=directory)
            os.fsync(directory)
