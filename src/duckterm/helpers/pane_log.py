"""Bounded tmux output spool. Live panes retain their own screen/scrollback."""

import os
import sys
import uuid
from pathlib import Path
from typing import BinaryIO

from duckterm.helpers.private_files import private_write

LIMIT = 8 * 1024 * 1024


def prepare_completion(path: Path) -> Path:
    previous = completion_for(path)
    if previous is not None:
        previous.unlink(missing_ok=True)
    token = uuid.uuid4().hex
    private_write(Path(str(path) + ".writer"), token)
    return Path(str(path) + "." + token + ".done")


def completion_for(path: Path) -> Path | None:
    try:
        token = Path(str(path) + ".writer").read_text()
    except FileNotFoundError:
        return None  # legacy panes have no completion protocol
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid pane writer generation")
    return Path(str(path) + "." + token + ".done")


def record(source: BinaryIO, path: Path, limit: int = LIMIT) -> None:
    # Two generations cap disk use even while the dashboard service is stopped.
    stream = None
    try:
        while chunk := os.read(source.fileno(), 4096):
            if stream is None:
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                stream = os.fdopen(fd, "ab", buffering=0)
            if stream.tell() + len(chunk) > limit:
                stream.close()
                stream = None
                os.replace(path, path.with_suffix(".previous"))
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                stream = os.fdopen(fd, "ab", buffering=0)
            stream.write(chunk)
    finally:
        if stream is not None:
            stream.close()


if __name__ == "__main__":
    status = "failed"
    try:
        record(sys.stdin.buffer, Path(sys.argv[1]))
        status = "complete"
    finally:
        if len(sys.argv) > 2:
            private_write(Path(sys.argv[2]), status)
