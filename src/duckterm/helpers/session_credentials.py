"""Per-session credential files shared by the broker and its local CLI client.

These are same-user capabilities, not a process sandbox. Never fall back to the
owner token. A stable filename lets already-running agents find their new token.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from duckterm.helpers import instance


def directory() -> Path:
    return instance.home() / "session-credentials"


def credential_path(key: str, root: Path | None = None) -> Path:
    return (root or directory()) / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def write_private(path: Path, value: dict[str, Any]) -> None:
    write_private_text(path, json.dumps(value))


def write_private_text(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".credential-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def launch_env(key: str) -> dict[str, str]:
    return {
        "DUCKTERM_SESSION_KEY": key,
        "DUCKTERM_SESSION_TOKEN_FILE": str(credential_path(key)),
        "DUCKTERM_URL": instance.server_url(),
    }


def client_credentials() -> tuple[str, str]:
    if os.environ.get("DUCKTERM_INTERNAL"):
        raise ValueError("internal helper agents cannot access session inboxes")
    key = os.environ.get("DUCKTERM_SESSION_KEY", "")
    if not key:
        raise ValueError("DUCKTERM_SESSION_KEY is missing; run this inside a Duckterm session")
    path = Path(os.environ.get("DUCKTERM_SESSION_TOKEN_FILE") or credential_path(key))
    try:
        credentials = json.loads(path.read_text())
        token = credentials["token"]
        if credentials["session_id"] != key or not isinstance(token, str) or not token:
            raise ValueError("credential does not belong to this session")
        connection = path.parent / "server.json"
        url = (
            json.loads(connection.read_text())["url"]
            if connection.exists()
            else instance.server_url()
        )
        if not isinstance(url, str):
            raise ValueError("invalid session server URL")
        return url.rstrip("/"), token
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(
            "session enrollment is unavailable; start or upgrade the Duckterm server"
        ) from exc
