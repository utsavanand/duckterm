"""Shared connectors with explicit identity, private storage and revocable runners.

Hosted machines delegate execution to the connector service. Local connector
selection is a convenience boundary: other processes owned by the same user
can still use that user's CLI logins and Keychain.
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from duckterm.agents import mcp_install
from duckterm.helpers.private_files import private_write

_GITHUB_ENV = "GITHUB_PERSONAL_ACCESS_TOKEN"
_GITHUB_IMAGE = "ghcr.io/github/github-mcp-server"
HARNESSES = ("claude-code", "codex")
NAMES = ("github", "railway", "porkbun")


# ── secret store ──


def _use_keychain() -> bool:
    return sys.platform == "darwin" and not os.environ.get("DUCKTERM_NO_KEYCHAIN")


def _keychain_service(name: str) -> str:
    return f"duckterm-connector-{name}"


def _secret_file(name: str) -> Path:
    from duckterm.helpers import paths

    return paths.home() / "connectors" / f"{name}.token"


def save_secret(name: str, token: str) -> None:
    from duckterm.helpers import keychain
    from duckterm.helpers.private_files import private_write

    if os.environ.get("DUCKTERM_HOSTED"):
        raise RuntimeError("Hosted credentials must be configured through the connector service")
    if _use_keychain():
        keychain.access(_keychain_service(name), "write", token)
    else:
        private_write(_secret_file(name), token)


def load_secret(name: str) -> str | None:
    from duckterm.helpers import keychain
    from duckterm.helpers.private_files import private_read

    if os.environ.get("DUCKTERM_HOSTED"):
        return None
    if _use_keychain():
        return keychain.access(_keychain_service(name), "read")
    return (private_read(_secret_file(name)) or "").strip() or None


def delete_secret(name: str) -> None:
    from duckterm.helpers import keychain

    if _use_keychain():
        keychain.access(_keychain_service(name), "delete")
    else:
        _secret_file(name).unlink(missing_ok=True)


# ── credential resolution ──


def _gh_cli_token() -> str | None:
    gh = shutil.which("gh")
    if gh is None:
        return None
    proc = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10)
    token = proc.stdout.strip()
    return token if proc.returncode == 0 and token else None


def _policy_path(name: str) -> Path:
    if name not in NAMES:
        raise ValueError(f"unknown connector {name!r}")
    return _secret_file(name).with_suffix(".json")


def policy(name: str, *, home: Path | None = None) -> dict[str, object]:
    path = _policy_path(name)
    if path.exists():
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise RuntimeError("invalid connector policy")
        return value
    # Preserve legacy installed behavior visibly until the user changes it.
    installed = mcp_install.claude_installed(name, home=home) or mcp_install.codex_installed(
        name, home=home
    )
    source = None
    if installed:
        source = "gh-cli" if name == "github" and _gh_cli_token() else "stored"
        if name == "railway":
            source = "railway-cli"
    return {
        "enabled": installed,
        "source": source,
        "identity": None,
        "write_access": installed and name == "porkbun",
        "generation": "legacy",
    }


def _save_policy(name: str, value: dict[str, object]) -> None:
    private_write(_policy_path(name), json.dumps(value))


def github_token(source: str | None = None) -> tuple[str, str] | None:
    chosen = source or policy("github").get("source")
    if chosen == "gh-cli":
        token = _gh_cli_token()
    elif chosen == "stored":
        token = load_secret("github")
    else:
        return None
    return (token, str(chosen)) if token else None


def github_identity(token: str) -> str:
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "duckterm"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            identity = json.load(resp).get("login")
            if not isinstance(identity, str) or not identity:
                raise RuntimeError("GitHub did not return a connected identity")
            return identity
    except urllib.error.HTTPError as exc:
        raise RuntimeError("GitHub rejected that token (api.github.com/user)") from exc
    except OSError as exc:
        raise RuntimeError(
            "Cannot verify GitHub identity; check your connection and retry"
        ) from exc


def github_server_argv() -> list[str]:
    """The actual MCP server process (run by `duckterm connector-run github`).
    Prefers the native binary; falls back to the official Docker image."""
    binary = shutil.which("github-mcp-server")
    if binary:
        return [binary, "stdio"]
    docker = shutil.which("docker")
    if docker:
        return [docker, "run", "-i", "--rm", "-e", _GITHUB_ENV, _GITHUB_IMAGE]
    raise RuntimeError(
        "no way to run the GitHub MCP server: install github-mcp-server "
        "(brew install github-mcp-server) or Docker"
    )


def _railway_cli() -> str | None:
    return shutil.which("railway")


def railway_logged_in() -> bool:
    cli = _railway_cli()
    if cli is None:
        return False
    proc = subprocess.run([cli, "whoami"], capture_output=True, timeout=10)
    return proc.returncode == 0


def _duckterm_bin() -> str:
    """Absolute path to the duckterm CLI for MCP entries — harnesses spawn MCP
    commands without a login shell, so PATH lookup is not reliable there. The
    pipx shim comes first: it survives upgrades and repo moves, while a dev
    venv's path dies with its checkout (see RETRO.md, hooks-127)."""
    shim = Path.home() / ".local" / "bin" / "duckterm"
    if shim.is_file():
        return str(shim)
    sibling = Path(sys.executable).with_name("duckterm")
    if sibling.is_file():
        return str(sibling)
    return shutil.which("duckterm") or "duckterm"


# ── enable / disable / status ──


def enable(
    name: str,
    token: str | None = None,
    secret: str | None = None,
    *,
    home: Path | None = None,
    source: str | None = None,
    write_access: bool = False,
) -> dict[str, object]:
    previous = policy(name, home=home)
    if os.environ.get("DUCKTERM_HOSTED"):
        raise RuntimeError("Manage hosted credentials through the separate connector administrator")
    identity: str | None = None
    if name == "github":
        source = source or ("stored" if token else None)
        if source not in ("stored", "gh-cli"):
            raise RuntimeError("Choose GitHub CLI login or stored personal access token")
        if token and source != "stored":
            raise RuntimeError("A pasted token requires the stored credential source")
        cred = (token, "stored") if token else github_token(source)
        if cred is None:
            raise RuntimeError("no GitHub credential for the selected source")
        github_server_argv()
        identity = github_identity(cred[0])
        if token:
            save_secret(name, token)
    elif name == "railway":
        if _railway_cli() is None:
            raise RuntimeError("Railway CLI not installed (https://docs.railway.com/guides/cli)")
        if source != "railway-cli":
            raise RuntimeError("Choose the Railway CLI login credential source")
        if not railway_logged_in():
            raise RuntimeError("Railway CLI not logged in: run `railway login`")
        proc = subprocess.run(
            [str(_railway_cli()), "whoami"], capture_output=True, text=True, timeout=10
        )
        identity = proc.stdout.strip()[:200] or "Railway CLI login (identity not returned)"
    elif name == "porkbun":
        source = "stored"
        if bool(token) != bool(secret):
            raise RuntimeError("Porkbun needs an API key AND secret key")
        key = token or load_secret("porkbun")
        secret_key = secret or load_secret("porkbun-secret")
        if not (key and secret_key):
            raise RuntimeError("Porkbun needs an API key AND secret key (porkbun.com/account/api)")
        if shutil.which("uvx") is None:
            raise RuntimeError("uvx not found — install uv (brew install uv)")
        if not porkbun_keys_valid(key, secret_key):
            raise RuntimeError("Porkbun rejected those keys (api.porkbun.com ping)")
        identity = "API key verified; Porkbun does not return an account identity"
        if token and secret:
            save_secret("porkbun", token)
            save_secret("porkbun-secret", secret)
    # Publish disabled first: an old live runner must not use a replaced identity.
    _save_policy(name, {**previous, "enabled": False, "generation": uuid.uuid4().hex})
    command, args = _duckterm_bin(), ["connector-run", name]
    try:
        mcp_install.claude_install(name, command, args, home=home)
        mcp_install.codex_install(name, command, args, home=home)
    except Exception:
        mcp_install.claude_remove(name, home=home)
        mcp_install.codex_remove(name, home=home)
        raise
    _save_policy(
        name,
        {
            "enabled": True,
            "source": source,
            "identity": identity,
            "write_access": name == "porkbun" and write_access,
            "generation": uuid.uuid4().hex,
        },
    )
    return status(name, home=home)


def porkbun_keys_valid(token: str, secret: str) -> bool:
    req = urllib.request.Request(
        "https://api.porkbun.com/api/json/v3/ping",
        data=json.dumps({"apikey": token, "secretapikey": secret}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "duckterm"},
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            body = json.loads(resp.read().decode())
            return bool(body.get("status") == "SUCCESS")
    except urllib.error.HTTPError:
        return False
    except OSError as exc:
        raise RuntimeError("Cannot verify Porkbun keys; check your connection and retry") from exc


def disable(name: str, *, home: Path | None = None) -> dict[str, object]:
    current = policy(name, home=home)
    if os.environ.get("DUCKTERM_HOSTED"):
        raise RuntimeError("Disable hosted connectors through the connector administrator")
    _save_policy(name, {**current, "enabled": False, "generation": uuid.uuid4().hex})
    mcp_install.claude_remove(name, home=home)
    mcp_install.codex_remove(name, home=home)
    return status(name, home=home)


def forget(name: str, *, home: Path | None = None) -> dict[str, object]:
    disable(name, home=home)
    delete_secret(name)
    if name == "porkbun":
        delete_secret("porkbun-secret")
    _save_policy(
        name,
        {
            "enabled": False,
            "source": None,
            "identity": None,
            "write_access": False,
            "generation": uuid.uuid4().hex,
        },
    )
    return status(name, home=home)


_REVOKE_URLS = {
    "github": "https://github.com/settings/tokens",
    "railway": "https://railway.com/account/tokens",
    "porkbun": "https://porkbun.com/account/api",
}


def status(name: str, *, home: Path | None = None) -> dict[str, object]:
    current = policy(name, home=home)
    installed = {
        "claude-code": mcp_install.claude_installed(name, home=home),
        "codex": mcp_install.codex_installed(name, home=home),
    }
    source = current.get("source")
    detail = None
    available: list[str] = []
    ready = False
    if name == "github":
        if shutil.which("gh"):
            available.append("gh-cli")
        available.append("stored")
        try:
            github_server_argv()
            ready = source is not None
        except RuntimeError:
            detail = "install github-mcp-server or Docker"
    elif name == "railway":
        available = ["railway-cli"]
        ready = _railway_cli() is not None
        detail = None if ready else "Railway CLI not installed"
    elif name == "porkbun":
        available = ["stored"]
        ready = shutil.which("uvx") is not None and source is not None
        detail = None if ready else "Needs uv and an API key + secret"
    descriptions = {
        "github": "Repos, PRs, issues, and actions",
        "railway": "Deployments, services, and logs",
        "porkbun": "Domains and DNS records",
    }
    title = {"github": "GitHub", "railway": "Railway", "porkbun": "Porkbun"}[name]
    return {
        "name": name,
        "title": title,
        "description": descriptions[name],
        "credential": source,
        "identity": current.get("identity"),
        "sources": available,
        "write_access": current.get("write_access", False),
        "installed": installed,
        "enabled": bool(current.get("enabled")) and all(installed.values()),
        "ready": ready,
        "detail": detail,
        "managed": bool(os.environ.get("DUCKTERM_HOSTED")),
        "revoke_url": (
            "https://github.com/settings/applications" if source == "gh-cli" else _REVOKE_URLS[name]
        ),
    }


def list_status(*, home: Path | None = None) -> list[dict[str, object]]:
    rows = [status(n, home=home) for n in NAMES]
    if os.environ.get("DUCKTERM_HOSTED"):
        from duckterm import connector_client

        try:
            remote = {row["name"]: row for row in connector_client.statuses()}
            for row in rows:
                entry = remote.get(row["name"], {})
                row.update(
                    enabled=bool(entry.get("enabled")),
                    ready=bool(entry.get("enabled")),
                    credential="secret-manager" if entry.get("enabled") else None,
                    identity=entry.get("identity"),
                    write_access=bool(entry.get("write_access")),
                    detail=None,
                )
        except (OSError, ValueError, RuntimeError):
            for row in rows:
                row.update(enabled=False, ready=False, detail="Connector service disconnected")
    return rows


def server_command(name: str, current: dict[str, object]) -> tuple[list[str], dict[str, str]]:
    env = dict(os.environ)
    # Ambient credentials must not override the explicitly selected source/policy.
    for env_key in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "PORKBUN_GET_MUDDY",
        "PORKBUN_API_KEY",
        "PORKBUN_SECRET_KEY",
        "RAILWAY_TOKEN",
        "RAILWAY_API_TOKEN",
    ):
        env.pop(env_key, None)
    if name == "github":
        cred = github_token(str(current.get("source")))
        if cred is None:
            raise RuntimeError("Selected GitHub credential is unavailable; reconnect it")
        env[_GITHUB_ENV] = cred[0]
        return github_server_argv(), env
    if name == "porkbun":
        key, secret = load_secret("porkbun"), load_secret("porkbun-secret")
        if not (key and secret):
            raise RuntimeError("Porkbun keys missing; reconnect it")
        env.update(PORKBUN_API_KEY=key, PORKBUN_SECRET_KEY=secret, PORKBUN_GET_MUDDY="false")
        args = [shutil.which("uvx") or "uvx", "porkbun-mcp"]
        if current.get("write_access"):
            args.append("--get-muddy")
        return args, env
    cli = _railway_cli()
    if not cli:
        raise RuntimeError("Railway CLI is unavailable")
    return [cli, "mcp"], env


def run(name: str) -> None:
    if os.environ.get("DUCKTERM_HOSTED"):
        from duckterm import connector_client

        connector_client.run(name)
        return
    current = policy(name)
    if not current.get("enabled"):
        raise SystemExit("Connector disabled in Duckterm")
    argv, env = server_command(name, current)
    # Keep the supervisor alive so Disable closes already-running MCP processes.
    # New process group also owns descendants; no PID files or PID-reuse hazards.
    proc = subprocess.Popen(argv, env=env, start_new_session=True)

    def stop(_signal: int, _frame: object) -> None:
        raise SystemExit(0)

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        while proc.poll() is None:
            latest = policy(name)
            if not latest.get("enabled") or latest.get("generation") != current.get("generation"):
                break
            time.sleep(0.2)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2)
        # A parent can exit while a descendant ignores TERM and retains credentials.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        signal.signal(signal.SIGTERM, previous)
    raise SystemExit(proc.returncode or 0)
