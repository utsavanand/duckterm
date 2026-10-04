"""Cooperative lifetime locks for pytest sockets, including killed test runs.

Only registered dead sockets are swept automatically; live servers are preserved. Legacy sockets have no
provable ownership and are deliberately left for manual investigation. Names
are never reused; a published lease stays locked until owner teardown finishes.
"""

import contextlib
import fcntl
import os
import re
import runpy
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path

PATTERN = re.compile(r"duckterm-pytest-[0-9]+-[0-9a-f]{32}\Z")
CLEANUP = Path(__file__).with_name("cleanup_test_sockets.py")


def registry() -> Path:
    root = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"duckterm-test-leases-{os.getuid()}"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("test socket lease directory must be private and owned")
    return root


@contextlib.contextmanager
def lease():
    name = f"duckterm-pytest-{os.getpid()}-{uuid.uuid4().hex}"
    root = registry()
    fd, pending = tempfile.mkstemp(prefix=".pending-", dir=root)
    path = root / name
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # Publish only after locking; sweepers never see an unlocked new lease.
        os.rename(pending, path)
        yield name
    finally:
        try:
            result = runpy.run_path(str(CLEANUP))["cleanup"](name)
            if not result["preserved"]:
                path.unlink(missing_ok=True)
        finally:
            os.close(fd)
            Path(pending).unlink(missing_ok=True)


def empty(path: Path) -> bool:
    """Query server-wide formats, including when there is no target session."""
    p = subprocess.run(
        [
            "tmux",
            "-S",
            str(path),
            "display-message",
            "-p",
            "sessions=#{S:1};clients=#{L:1};pid=#{pid}",
        ],
        capture_output=True,
        text=True,
        timeout=3,
    )
    # The display-message command is itself one client; any second client
    # preserves the server, including unattached control clients.
    return (
        p.returncode == 0
        and re.fullmatch(r"sessions=;clients=1;pid=[0-9]+\n?", p.stdout) is not None
    )


def sweep() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"removed": [], "preserved": []}
    root = registry()
    sockets = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
    cleanup = runpy.run_path(str(CLEANUP))["cleanup"]
    for path in sorted(root.iterdir()):
        if not PATTERN.fullmatch(path.name):
            continue
        fd = None
        try:
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                result["preserved"].append(path.name)
                continue
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # The lock excludes cooperating launchers, but not new tmux clients.
            # Never terminate a live server here: even an empty server can gain
            # a session after a query. A released lease does not change that.
            candidates = [
                p
                for p in sockets.glob(path.name + "*")
                if p.name == path.name or p.name.startswith(path.name + "-")
            ]
            preserved = []
            for candidate in candidates:
                dead = cleanup(candidate.name, stop=False, exact=True)
                result["removed"].extend(dead["removed"])
                preserved.extend(dead["preserved"])
            result["preserved"].extend(preserved)
            if not preserved:
                path.unlink()
        except FileNotFoundError:
            pass
        except (OSError, subprocess.TimeoutExpired):
            result["preserved"].append(path.name)
        finally:
            if fd is not None:
                os.close(fd)
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(sweep()))
