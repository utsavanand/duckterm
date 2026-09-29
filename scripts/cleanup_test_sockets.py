"""Remove confirmed dead test sockets; --owned also stops this run's servers.

Never use a failed tmux command alone as evidence of a dead server. Unknown
errors, live servers, non-sockets, symlinks and production names are preserved.
A later --sweep-dead handles leftovers from uncatchable process termination.
"""

import argparse
import errno
import os
import socket
import stat
import subprocess
import time
from pathlib import Path

PREFIXES = (
    "duckterm-pytest-",
    "rd-e2e-",
    "qa95-",
    "unified-qa-",
    "copy-probe-",
    "duckterm-native-transfer-",
    "duckterm-bundle-",
    "duckterm-test-",
    "qa-bundle-",
    "duckterm-rehearsal-",
)


def cleanup(owned: str | None = None) -> dict[str, list[str]]:
    if owned is not None and (not owned.startswith(PREFIXES) or "/" in owned):
        raise ValueError("only named test namespaces can be owned")
    directory = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
    result: dict[str, list[str]] = {"removed": [], "preserved": []}
    for path in sorted(directory.glob("*")):
        if not path.name.startswith(PREFIXES):
            continue
        if owned and path.name != owned and not path.name.startswith(owned + "-"):
            continue
        try:
            before = path.lstat()
            if not stat.S_ISSOCK(before.st_mode) or before.st_uid != os.getuid():
                result["preserved"].append(path.name)
                continue
            if owned:
                subprocess.run(
                    ["tmux", "-S", str(path), "kill-server"], capture_output=True, timeout=3
                )
            for _ in range(20 if owned else 1):
                probe = subprocess.run(
                    ["tmux", "-S", str(path), "list-sessions"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                dead = "no server running on " in probe.stderr or (
                    "error connecting to " in probe.stderr
                    and "(No such file or directory)" in probe.stderr
                )
                if probe.returncode and dead:
                    with socket.socket(socket.AF_UNIX) as client:
                        client.settimeout(0.2)
                        error = client.connect_ex(str(path))
                    after = path.lstat()
                    if error in (errno.ECONNREFUSED, errno.ENOENT) and (
                        before.st_dev,
                        before.st_ino,
                    ) == (after.st_dev, after.st_ino):
                        # Best effort: a same-user process can still bind/listen
                        # between the last probe/lstat and unlink. POSIX has no
                        # atomic "unlink only if this inode is still dead".
                        # Run dead sweeps while test launches are quiescent;
                        # owned teardown must exclusively own its namespace.
                        path.unlink()
                        result["removed"].append(path.name)
                    else:
                        result["preserved"].append(path.name)
                    break
                if not owned:
                    result["preserved"].append(path.name)
                    break
                time.sleep(0.025)
            else:
                result["preserved"].append(path.name)
        except FileNotFoundError:
            if path.exists():
                result["preserved"].append(path.name)
        except (OSError, subprocess.TimeoutExpired):
            result["preserved"].append(path.name)
    return result


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--owned")
    group.add_argument("--sweep-dead", action="store_true")
    args = parser.parse_args()
    print(json.dumps(cleanup(args.owned)))
