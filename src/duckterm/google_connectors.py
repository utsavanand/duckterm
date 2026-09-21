"""Google provider launch adapters. Provider credentials stay on the connector host."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

GMAIL_PACKAGE = "@artymclabin/gmail-mcp@1.2.3"
GCP_PACKAGE = "@google-cloud/gcloud-mcp@0.5.3"


def node_runner() -> str:
    npx, node = shutil.which("npx"), shutil.which("node")
    if not npx or not node:
        raise RuntimeError("Install Node.js 22+ with npm")
    try:
        result = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=3, check=True
        )
        major = int(result.stdout.strip().removeprefix("v").split(".")[0])
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("Could not check Node.js; install Node.js 22+ with npm") from exc
    if major < 22:
        raise RuntimeError("Install Node.js 22+ with npm")
    return npx


def gmail_paths() -> tuple[Path, Path]:
    root = Path.home() / ".gmail-mcp"
    return (
        Path(os.environ.get("GMAIL_OAUTH_PATH", str(root / "gcp-oauth.keys.json"))).expanduser(),
        Path(os.environ.get("GMAIL_CREDENTIALS_PATH", str(root / "credentials.json"))).expanduser(),
    )


def gmail_ready() -> bool:
    keys, credentials = gmail_paths()
    try:
        oauth = json.loads(keys.read_text())
        client = oauth.get("installed") or oauth.get("web") or {}
        saved = json.loads(credentials.read_text())
        tokens = saved.get("tokens", saved)
        # Reject legacy unscoped credentials: upstream treats them as full access.
        return bool(
            client.get("client_id")
            and client.get("client_secret")
            and tokens.get("refresh_token")
            and saved.get("scopes") == ["gmail.readonly"]
        )
    except (OSError, ValueError, AttributeError):
        return False


def gcp_ready() -> bool:
    cli = shutil.which("gcloud")
    if not cli:
        return False
    try:
        proc = subprocess.run(
            [cli, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def command(name: str) -> tuple[list[str], dict[str, str]]:
    npx = node_runner()
    env = dict(os.environ)
    if name == "gmail":
        if not gmail_ready():
            raise RuntimeError("Set up Google OAuth, then run `duckterm connector-auth gmail`")
        keys, credentials = gmail_paths()
        env.update(
            GMAIL_OAUTH_PATH=str(keys.resolve()), GMAIL_CREDENTIALS_PATH=str(credentials.resolve())
        )
        return [npx, "--yes", GMAIL_PACKAGE], env
    if name == "gcp":
        if not gcp_ready():
            raise RuntimeError("Install the Google Cloud CLI, then run `gcloud auth login`")
        return [npx, "--yes", GCP_PACKAGE], env
    raise ValueError("Unknown Google connector")


def authenticate_gmail() -> int:
    npx = node_runner()
    keys, credentials = gmail_paths()
    if not keys.is_file():
        raise RuntimeError(
            "Create a Desktop OAuth client with the Gmail API enabled and save its JSON at "
            f"{keys}; see the Gmail connector's setup instructions"
        )
    credentials.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = dict(
        os.environ,
        GMAIL_OAUTH_PATH=str(keys.resolve()),
        GMAIL_CREDENTIALS_PATH=str(credentials.resolve()),
    )
    # Upstream imports OAuth files from cwd before honoring the explicit paths.
    # Never let an agent repository replace the selected OAuth client.
    with tempfile.TemporaryDirectory(prefix="duckterm-gmail-auth-") as cwd:
        result = subprocess.run(
            [npx, "--yes", GMAIL_PACKAGE, "auth", "--scopes=gmail.readonly"], env=env, cwd=cwd
        )
    if result.returncode == 0 and not gmail_ready():
        raise RuntimeError("Gmail sign-in did not save usable read-only credentials")
    return result.returncode
