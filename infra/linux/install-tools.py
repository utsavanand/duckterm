"""Install pinned official Linux executables, verifying release asset digests."""

import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

TOOLS = {
    "codex": (
        "https://github.com/openai/codex/releases/download/rust-v0.155.1/codex-x86_64-unknown-linux-musl.tar.gz",
        "a0ef8b2debc3bf747e07b1a039354de31300ac0dcc2276498ba281470b5d9115",
        "codex-x86_64-unknown-linux-musl",
    ),
    "github-mcp-server": (
        "https://github.com/github/github-mcp-server/releases/download/v1.12.2/github-mcp-server_Linux_x86_64.tar.gz",
        "95843162759da2c31dde082dd145be35db82164594796c294414b69790c2290e",
        "github-mcp-server",
    ),
    "railway": (
        "https://github.com/railwayapp/cli/releases/download/v5.58.0/railway-v5.58.0-x86_64-unknown-linux-musl.tar.gz",
        "9b054087fc70e2c02e5aa6a178156908ef105d4fb91939dbe349791319542593",
        "railway",
    ),
}


def install(name: str, destination: Path) -> None:
    url, expected, executable = TOOLS[name]
    with urllib.request.urlopen(url, timeout=120) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise RuntimeError("Release checksum did not match")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        matches = [
            item
            for item in archive.getmembers()
            if item.isfile() and Path(item.name).name == executable
        ]
        if len(matches) != 1:
            raise RuntimeError("Release did not contain exactly one expected executable")
        stream = archive.extractfile(matches[0])
        assert stream is not None
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / name
        target.write_bytes(stream.read())
        target.chmod(0o755)
    print(json.dumps({"installed": name, "sha256": expected}))


if __name__ == "__main__":
    install(sys.argv[1], Path(sys.argv[2]))
