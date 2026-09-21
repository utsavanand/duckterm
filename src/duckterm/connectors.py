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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token)
    path.chmod(0o600)


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
    if name == "github":
        return _enable_github(token, home=home)
    if name == "railway":
        return _enable_railway(home=home)
    if name == "porkbun":
        return _enable_porkbun(token, secret, home=home)
    raise ValueError(f"unknown connector {name!r}")


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
    mcp_install.claude_remove(name, home=home)
    mcp_install.codex_remove(name, home=home)
    delete_secret(name)
    if name == "porkbun":
        delete_secret("porkbun-secret")
    return status(name, home=home)


def status(name: str, *, home: Path | None = None) -> dict[str, object]:
    """Cheap, local-only status — no network calls (this backs a GET the UI
    polls). Credential validity is checked once, at enable time."""
    installed = {
        "claude-code": mcp_install.claude_installed(name, home=home),
        "codex": mcp_install.codex_installed(name, home=home),
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
    return [status(n, home=home) for n in NAMES]


def run(name: str) -> None:
    """`duckterm connector-run <name>`: exec the MCP server with the credential
    resolved NOW — this is what keeps tokens out of the harness config files."""
    if name == "github":
        cred = github_token()
        if cred is None:
            print(
                "duckterm: no GitHub credential (gh auth login, or re-enable the connector)",
                file=sys.stderr,
            )
            raise SystemExit(1)
        argv = github_server_argv()
        os.execvpe(argv[0], argv, {**os.environ, _GITHUB_ENV: cred[0]})
    if name == "porkbun":
        key, secret = load_secret("porkbun"), load_secret("porkbun-secret")
        if not (key and secret):
            print("duckterm: Porkbun keys missing — re-enable the connector", file=sys.stderr)
            raise SystemExit(1)
        uvx = shutil.which("uvx") or "uvx"
        env = {**os.environ, "PORKBUN_API_KEY": key, "PORKBUN_SECRET_KEY": secret}
        os.execvpe(uvx, [uvx, "porkbun-mcp", "--get-muddy"], env)
    raise SystemExit(f"duckterm: connector {name!r} has no runnable server")
