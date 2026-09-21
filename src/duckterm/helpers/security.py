"""Request-gating for the local server.

The server binds 127.0.0.1, which keeps it off the network but does NOT protect
it from the browser: any web page the user visits can issue cross-origin
requests to http://127.0.0.1:4300. The browser blocks the attacker from reading
the *responses* (no CORS headers are sent), but a "simple" cross-origin POST
still reaches the handler — so a malicious tab could drive command-executing
endpoints blind. This module closes that hole with two cheap checks:

  1. Origin/Referer: reject any cross-origin request outright.
  2. A per-install secret token, generated on first run and stored 0600 under
     ~/.duckterm, required on every state-changing request. A blind CSRF
     cannot read the token, so it cannot forge the header.

Plus input validators for values that flow into shells, file paths, and
AppleScript.
"""

import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from duckterm.helpers import paths

TOKEN_HEADER = "x-duckterm-token"

# Host validation prevents a rebound attacker domain from reading the dashboard
# token. Browser origins must also match the requested host AND port.
_LOCAL_HOST_RE = re.compile(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?")

# session_key flows into shell strings (heartbeat) and DB rows. Constrain it to
# characters that are inert in a shell and a path.
_SESSION_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# A tty reported by a tab is injected into AppleScript; it has a fixed shape.
_TTY_RE = re.compile(r"^/dev/tty[a-z0-9]+$")
# Snapshot ids are server-minted as snap-<digits>; nothing else is valid.
_SNAPSHOT_ID_RE = re.compile(r"^snap-[0-9]+$")


def valid_session_key(key: str | None) -> bool:
    return bool(key) and key not in {".", ".."} and _SESSION_KEY_RE.fullmatch(key or "") is not None


def valid_tty(tty: str | None) -> bool:
    return bool(tty) and _TTY_RE.fullmatch(tty or "") is not None


def valid_snapshot_id(snapshot_id: str | None) -> bool:
    return bool(snapshot_id) and _SNAPSHOT_ID_RE.fullmatch(snapshot_id or "") is not None


def new_session_key(prefix: str) -> str:
    """An unguessable session key, so the input/attach endpoints can't be
    targeted by guessing a key like `new-<timestamp>`."""
    return f"{prefix}-{secrets.token_hex(8)}"


def host_allowed(host: str) -> bool:
    if _LOCAL_HOST_RE.fullmatch(host) is None:
        return False
    try:
        port = urlsplit("http://" + host).port
        return port is None or 0 < port <= 65535
    except ValueError:
        return False


def origin_allowed(headers: dict[str, str]) -> bool:
    """Allow CLI requests without browser headers, or an exact HTTP origin."""
    value = headers.get("origin", headers.get("referer"))
    if value is None:
        return True
    host = headers.get("host", "")
    if not host_allowed(host):
        return False
    try:
        source = urlsplit(value)
        target = urlsplit("http://" + host)
        if "origin" in headers and (source.path or source.query or source.fragment):
            return False
        return (
            source.scheme == "http"
            and host_allowed(source.netloc)
            and source.hostname == target.hostname
            and (source.port or 80) == (target.port or 80)
        )
    except ValueError:
        return False


def load_or_create_token() -> str:
    """The per-install secret, created on first run and persisted 0600. Shared
    with the dashboard (injected into its HTML) and the hook script (via env)."""
    path = _token_path()
    if path.exists():
        existing = path.read_text().strip()
        if existing:
            path.chmod(0o600)
            return existing
    token = secrets.token_urlsafe(32)
    write_private_text(path, token)
    return token


def write_private_text(path: Path, text: str) -> None:
    """Publish a complete secret with 0600 permissions from its first write."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".secret-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def token_valid(headers: dict[str, str], token: str) -> bool:
    supplied = headers.get(TOKEN_HEADER, "")
    return bool(token) and secrets.compare_digest(supplied.encode(), token.encode())


def _token_path() -> Path:
    return paths.home() / "token"
