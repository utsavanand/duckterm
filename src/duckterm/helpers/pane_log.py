"""Bounded tmux output spool. Live panes retain their own screen/scrollback."""

import os
import sys
from pathlib import Path
from typing import BinaryIO

LIMIT = 8 * 1024 * 1024


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
    record(sys.stdin.buffer, Path(sys.argv[1]))
