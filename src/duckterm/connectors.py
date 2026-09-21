"""Connectors: one switch that wires an external service into every harness.

Enabling a connector (a) resolves a credential — preferring an existing CLI
login over storing anything — and (b) registers the service's MCP server in
the claude-code and codex user configs (agents/mcp_install). Disabling removes
the entries and any stored secret.

Credential handling: the harness config files are plaintext on disk, so a
token must never be written into them. GitHub's MCP entry therefore runs
through `duckterm connector-run github`, which resolves the token at server
launch (gh CLI first, then the secret store) and passes it via the process
environment. Railway needs no credential at all — its MCP server is bundled in
the Railway CLI and reuses the CLI login.
Hugging Face uses a pinned HTTP bridge with optional launch-time credentials
and the hosted search preset; anonymous discovery requires no token.

Secret store: macOS Keychain (`security`), or a 0600 file under
DUCKTERM_HOME/connectors on other platforms / when DUCKTERM_NO_KEYCHAIN is set
(tests, CI).
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from duckterm.agents import mcp_install
from duckterm.helpers.security import write_private_text

_GITHUB_ENV = "GITHUB_PERSONAL_ACCESS_TOKEN"
_GITHUB_IMAGE = "ghcr.io/github/github-mcp-server"
_HF_URL = "https://huggingface.co/mcp?bouquet=search"
_MCP_REMOTE = "mcp-remote@0.1.38"
HARNESSES = ("claude-code", "codex")
NAMES = ("github", "railway", "porkbun", "huggingface", "gmail", "gcp")


# ── secret store ──


def _use_keychain() -> bool:
    return sys.platform == "darwin" and not os.environ.get("DUCKTERM_NO_KEYCHAIN")


def _keychain_service(name: str) -> str:
    return f"duckterm-connector-{name}"


def _secret_file(name: str) -> Path:
    from duckterm.helpers import paths

    return paths.home() / "connectors" / f"{name}.token"


def save_secret(name: str, token: str) -> None:
    if _use_keychain():
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-a",
                "duckterm",
                "-s",
                _keychain_service(name),
                "-w",
                token,
            ],
            check=True,
            capture_output=True,
        )
        return
    path = _secret_file(name)
    write_private_text(path, token)


def load_secret(name: str) -> str | None:
    if _use_keychain():
        proc = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                "duckterm",
                "-s",
                _keychain_service(name),
                "-w",
            ],
            capture_output=True,
            text=True,
        )
        token = proc.stdout.strip()
        return token if proc.returncode == 0 and token else None
    path = _secret_file(name)
    if not path.exists():
        return None
    return path.read_text().strip() or None


def delete_secret(name: str) -> None:
    if _use_keychain():
        subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-a",
                "duckterm",
                "-s",
                _keychain_service(name),
            ],
            capture_output=True,
        )
        return
    _secret_file(name).unlink(missing_ok=True)


# ── credential resolution ──


def _gh_cli_token() -> str | None:
    gh = shutil.which("gh")
    if gh is None:
        return None
    proc = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10)
    token = proc.stdout.strip()
    return token if proc.returncode == 0 and token else None


def github_token() -> tuple[str, str] | None:
    """(token, source) — the gh CLI login wins over a stored token, so a
    rotated gh login is picked up without touching the connector."""
    token = _gh_cli_token()
    if token:
        return token, "gh-cli"
    token = load_secret("github")
    if token:
        return token, "stored"
    return None


def github_identity(token: str) -> str:
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "duckterm"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            identity = json.load(response).get("login")
        if not isinstance(identity, str) or not identity:
            raise RuntimeError("GitHub did not return an identity")
        return identity
    except (OSError, ValueError) as exc:
        raise RuntimeError("Could not verify the selected GitHub identity") from exc


def github_token_valid(token: str) -> bool:
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "duckterm"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return bool(resp.status == 200)
    except urllib.error.HTTPError:
        return False
    except OSError:
        # Offline is not "invalid" — don't block enable on network weather.
        return True


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


def huggingface_token() -> tuple[str, str] | None:
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        return token, "HF_TOKEN"
    stored_token = load_secret("huggingface")
    return (stored_token, "stored") if stored_token else None


def huggingface_token_valid(token: str) -> bool:
    req = urllib.request.Request(
        "https://huggingface.co/api/whoami-v2",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "duckterm"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return bool(resp.status == 200)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False
        raise RuntimeError("Hugging Face token validation is unavailable; try again") from exc
    except OSError as exc:
        raise RuntimeError("Could not reach Hugging Face to validate the token; try again") from exc


def huggingface_server_argv() -> list[str]:
    npx = shutil.which("npx")
    node = shutil.which("node")
    if npx is None or node is None:
        raise RuntimeError("install Node.js 22+ with npm to connect Hugging Face")
    try:
        result = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=3, check=True
        )
        major = int(result.stdout.strip().removeprefix("v").split(".")[0])
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("could not check Node.js version; install Node.js 22+ with npm") from exc
    if major < 22:
        raise RuntimeError("Hugging Face requires Node.js 22+ with npm")
    return [npx, "--yes", _MCP_REMOTE, _HF_URL, "--transport", "http-only", "--silent"]


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
) -> dict[str, object]:
    from duckterm import connector_client

    if name not in NAMES:
        raise ValueError(f"unknown connector {name!r}")
    if connector_client.configured():
        if token or secret:
            raise RuntimeError("Configure provider credentials on the shared connector host")
        available = {row["name"]: row for row in connector_client.statuses()}
        if not available.get(name, {}).get("enabled"):
            raise RuntimeError("Connector is not enabled for this workspace on the shared host")
        _install(name, home=home)
    elif name == "github":
        _enable_github(token, home=home)
    elif name == "railway":
        _enable_railway(home=home)
    elif name == "porkbun":
        _enable_porkbun(token, secret, home=home)
    elif name == "huggingface":
        _enable_huggingface(token, home=home)
    else:
        from duckterm import google_connectors

        google_connectors.command(name)
        _install(name, home=home)
    _set_enabled(name, True)
    return status(name, home=home)


def _install(name: str, *, home: Path | None = None) -> None:
    command, args = _duckterm_bin(), ["connector-run", name]
    mcp_install.claude_install(name, command, args, home=home)
    mcp_install.codex_install(name, command, args, home=home)


def _set_enabled(name: str, enabled: bool) -> None:
    import uuid

    write_private_text(
        _secret_file(name).with_suffix(".state.json"),
        json.dumps({"enabled": enabled, "generation": uuid.uuid4().hex}),
    )


def execution_state(name: str) -> dict[str, object]:
    """Revocation state read by the shared host; legacy registrations remain usable."""
    if name not in NAMES:
        raise ValueError("Unknown connector")
    path = _secret_file(name).with_suffix(".state.json")
    if path.exists():
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise RuntimeError("Invalid connector state")
        return value
    return {
        "enabled": any((mcp_install.claude_installed(name), mcp_install.codex_installed(name))),
        "generation": "legacy",
    }


def _enable_huggingface(token: str | None, *, home: Path | None) -> dict[str, object]:
    huggingface_server_argv()
    if token:
        if not huggingface_token_valid(token):
            raise RuntimeError("Hugging Face rejected that token (api/whoami-v2)")
        save_secret("huggingface", token)
    # Public discovery needs no account. Resolve optional credentials only at launch.
    command, args = _duckterm_bin(), ["connector-run", "huggingface"]
    mcp_install.claude_install("huggingface", command, args, home=home)
    mcp_install.codex_install("huggingface", command, args, home=home)
    return status("huggingface", home=home)


def _enable_github(token: str | None, *, home: Path | None) -> dict[str, object]:
    if token:
        if not github_token_valid(token):
            raise RuntimeError("GitHub rejected that token (api.github.com/user)")
        save_secret("github", token)
    cred = github_token()
    if cred is None:
        raise RuntimeError(
            "no GitHub credential: run `gh auth login`, or paste a personal access token"
        )
    github_server_argv()  # raises with an install hint if nothing can run it
    # The shim resolves the token at launch, so no secret lands in the configs.
    command, args = _duckterm_bin(), ["connector-run", "github"]
    mcp_install.claude_install("github", command, args, home=home)
    mcp_install.codex_install("github", command, args, home=home)
    return status("github", home=home)


def _enable_railway(*, home: Path | None) -> dict[str, object]:
    cli = _railway_cli()
    if cli is None:
        raise RuntimeError("Railway CLI not installed (https://docs.railway.com/guides/cli)")
    if not railway_logged_in():
        raise RuntimeError("Railway CLI not logged in: run `railway login`")
    # Railway's MCP server ships inside the CLI and reuses its login.
    mcp_install.claude_install("railway", cli, ["mcp"], home=home)
    mcp_install.codex_install("railway", cli, ["mcp"], home=home)
    return status("railway", home=home)


def _enable_porkbun(
    token: str | None, secret: str | None, *, home: Path | None
) -> dict[str, object]:
    """Porkbun (DNS/domains): two credentials (API key + secret), no CLI to
    reuse. Runs major/porkbun-mcp via uvx with write mode on — the point of
    connecting it is letting the agent manage records."""
    if token and secret:
        if not porkbun_keys_valid(token, secret):
            raise RuntimeError("Porkbun rejected those keys (api.porkbun.com ping)")
        save_secret("porkbun", token)
        save_secret("porkbun-secret", secret)
    if not (load_secret("porkbun") and load_secret("porkbun-secret")):
        raise RuntimeError(
            "Porkbun needs an API key AND secret key (porkbun.com/account/api; "
            "also enable API access per domain)"
        )
    if shutil.which("uvx") is None:
        raise RuntimeError("uvx not found — install uv (brew install uv)")
    command, args = _duckterm_bin(), ["connector-run", "porkbun"]
    mcp_install.claude_install("porkbun", command, args, home=home)
    mcp_install.codex_install("porkbun", command, args, home=home)
    return status("porkbun", home=home)


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
    except OSError:
        return True  # offline is not "invalid"


def disable(name: str, *, home: Path | None = None) -> dict[str, object]:
    if name not in NAMES:
        raise ValueError(f"unknown connector {name!r}")
    _set_enabled(name, False)
    mcp_install.claude_remove(name, home=home)
    mcp_install.codex_remove(name, home=home)
    from duckterm import connector_client

    if connector_client.configured():
        return status(name, home=home)
    delete_secret(name)
    if name == "porkbun":
        delete_secret("porkbun-secret")
    return status(name, home=home)


def status(name: str, *, home: Path | None = None) -> dict[str, object]:
    """Cheap, local-only status — no network calls (this backs a GET the UI
    polls). Credential validity is checked once, at enable time."""
    from duckterm import connector_client

    if connector_client.configured():
        return next(row for row in list_status(home=home) if row["name"] == name)
    installed = {
        "claude-code": mcp_install.claude_installed(name, home=home),
        "codex": mcp_install.codex_installed(name, home=home),
    }
    if name in ("gmail", "gcp"):
        from duckterm import google_connectors

        try:
            google_connectors.command(name)
            google_ready, google_detail = True, None
        except RuntimeError as exc:
            google_ready, google_detail = False, str(exc)
        return {
            "name": name,
            "title": "Gmail" if name == "gmail" else "Google Cloud",
            "description": (
                "Search and read personal Gmail"
                if name == "gmail"
                else "Google Cloud resources via your active gcloud account"
            ),
            "credential": (
                ("google-oauth" if name == "gmail" else "gcloud-cli") if google_ready else None
            ),
            "installed": installed,
            "enabled": all(installed.values()),
            "ready": google_ready,
            "detail": google_detail,
        }
    if name == "huggingface":
        hf_cred = huggingface_token()
        try:
            huggingface_server_argv()
            ready, hf_detail = True, "Public discovery; token optional"
        except RuntimeError as exc:
            ready, hf_detail = False, str(exc)
        return {
            "name": name,
            "title": "Hugging Face",
            "description": "Discover models, datasets, and documentation on Hugging Face",
            "credential": hf_cred[1] if hf_cred else None,
            "installed": installed,
            "enabled": all(installed.values()),
            "ready": ready,
            "detail": hf_detail,
        }
    if name == "github":
        cred = None
        if _gh_cli_token():
            cred = "gh-cli"
        elif load_secret("github"):
            cred = "stored"
        try:
            github_server_argv()
            runnable = True
        except RuntimeError:
            runnable = False
        detail = None if runnable else "install github-mcp-server or Docker"
        return {
            "name": "github",
            "title": "GitHub",
            "description": "Repos, PRs, issues, and actions via the official GitHub MCP server",
            "credential": cred,
            "installed": installed,
            "enabled": all(installed.values()),
            "ready": cred is not None and runnable,
            "detail": detail,
        }
    if name == "railway":
        cli = _railway_cli()
        logged_in = railway_logged_in()
        detail = None
        if cli is None:
            detail = "Railway CLI not installed"
        elif not logged_in:
            detail = "run `railway login`"
        return {
            "name": "railway",
            "title": "Railway",
            "description": "Deployments, services, and logs via the Railway CLI's MCP server",
            "credential": "railway-cli" if logged_in else None,
            "installed": installed,
            "enabled": all(installed.values()),
            "ready": logged_in,
            "detail": detail,
        }
    if name == "porkbun":
        has_keys = bool(load_secret("porkbun") and load_secret("porkbun-secret"))
        runnable = shutil.which("uvx") is not None
        detail = None
        if not runnable:
            detail = "install uv (brew install uv)"
        elif not has_keys:
            detail = "needs API key + secret (porkbun.com/account/api)"
        return {
            "name": "porkbun",
            "title": "Porkbun",
            "description": "Domains and DNS records via porkbun-mcp (writes enabled)",
            "credential": "stored" if has_keys else None,
            "installed": installed,
            "enabled": all(installed.values()),
            "ready": has_keys and runnable,
            "detail": detail,
        }
    raise ValueError(f"unknown connector {name!r}")


def list_status(*, home: Path | None = None) -> list[dict[str, object]]:
    from duckterm import connector_client

    if not connector_client.configured():
        return [status(n, home=home) for n in NAMES]
    titles = {
        "github": "GitHub",
        "railway": "Railway",
        "porkbun": "Porkbun",
        "huggingface": "Hugging Face",
        "gmail": "Gmail",
        "gcp": "Google Cloud",
    }
    try:
        remote = {row["name"]: row for row in connector_client.statuses()}
        detail = None
    except (OSError, ValueError, RuntimeError):
        remote, detail = {}, "Shared connector service unavailable"
    return [
        {
            "name": name,
            "title": titles[name],
            "description": "Provided by your shared connector host",
            "credential": "shared-host" if remote.get(name, {}).get("enabled") else None,
            "installed": {
                "claude-code": mcp_install.claude_installed(name, home=home),
                "codex": mcp_install.codex_installed(name, home=home),
            },
            "enabled": bool(remote.get(name, {}).get("enabled"))
            and mcp_install.claude_installed(name, home=home)
            and mcp_install.codex_installed(name, home=home),
            "ready": bool(remote.get(name, {}).get("enabled")),
            "managed": True,
            "detail": detail
            or (None if remote.get(name, {}).get("enabled") else "Not enabled for this workspace"),
        }
        for name in NAMES
    ]


def server_command(name: str) -> tuple[list[str], dict[str, str]]:
    """Resolve provider credentials only on the execution host."""
    env = dict(os.environ)
    if name == "huggingface":
        argv = huggingface_server_argv()
        cred = huggingface_token()
        if cred:
            env["DUCKTERM_HF_AUTH"] = f"Bearer {cred[0]}"
            argv.extend(["--header", "Authorization:${DUCKTERM_HF_AUTH}"])
        return argv, env
    if name == "github":
        cred = github_token()
        if cred is None:
            raise RuntimeError("No GitHub credential; reconnect the connector")
        env[_GITHUB_ENV] = cred[0]
        return github_server_argv(), env
    if name == "railway":
        cli = _railway_cli()
        if not cli:
            raise RuntimeError("Railway CLI not installed")
        return [cli, "mcp"], env
    if name == "porkbun":
        key, secret = load_secret("porkbun"), load_secret("porkbun-secret")
        if not (key and secret):
            raise RuntimeError("Porkbun keys missing; reconnect the connector")
        env.update(PORKBUN_API_KEY=key, PORKBUN_SECRET_KEY=secret)
        return [shutil.which("uvx") or "uvx", "porkbun-mcp", "--get-muddy"], env
    if name in ("gmail", "gcp"):
        from duckterm import google_connectors

        return google_connectors.command(name)
    raise ValueError(f"Unknown connector {name!r}")


def run(name: str) -> None:
    from duckterm import connector_client

    if name not in NAMES:
        raise SystemExit(f"Unknown connector {name!r}")
    if connector_client.configured():
        connector_client.run(name)
        return
    argv, env = server_command(name)
    if name == "gmail":
        import tempfile

        with tempfile.TemporaryDirectory(prefix="duckterm-gmail-") as cwd:
            raise SystemExit(subprocess.call(argv, env=env, cwd=cwd))
    os.execvpe(argv[0], argv, env)
