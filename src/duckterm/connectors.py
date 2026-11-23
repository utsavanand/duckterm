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
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from duckterm.agents import connector_dispatch, mcp_install
from duckterm.helpers import instance
from duckterm.helpers.private_files import private_write

_GITHUB_ENV = "GITHUB_PERSONAL_ACCESS_TOKEN"
_GITHUB_IMAGE = "ghcr.io/github/github-mcp-server"
HARNESSES = ("claude-code", "codex")
# One connector. Railway and Porkbun were MCP pass-throughs only; nothing
# inside Duckterm read them, so removing them cost no features.
NAMES = ("github",)


# ── secret store ──


def _use_keychain() -> bool:
    return sys.platform == "darwin" and not os.environ.get("DUCKTERM_NO_KEYCHAIN")


def _keychain_service(name: str) -> str:
    import hashlib

    from duckterm.helpers import instance

    root = instance.home().resolve()
    if root == (Path.home() / ".duckterm").resolve():
        return f"duckterm-connector-{name}"
    # Home, rather than only the short instance id, also isolates explicit
    # custom profiles with no DUCKTERM_INSTANCE. Full's existing names survive.
    scope = hashlib.sha256(str(root).encode()).hexdigest()[:24]
    return f"duckterm-connector-{scope}-{name}"


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


def _claude_registered(name: str, *, home: Path | None = None) -> bool:
    return mcp_install.managed_installed(name, "claude-code", home=home)


def _codex_registered(name: str, *, home: Path | None = None) -> bool:
    return mcp_install.managed_installed(name, "codex", home=home)


def unreachable_harnesses(name: str, *, home: Path | None = None) -> list[str]:
    """Chosen harnesses with no MCP entry, so an agent there cannot resolve it.

    Separate from the policy flag on purpose. Both make a connector unusable by
    agents, but they are different situations with different fixes: the owner
    turned it off, versus an entry went missing behind the owner's back. A
    single boolean cannot say which, so the panel could not either -- it
    described Disable and a lost registration in the same words.

    Fail-closed: a missing or unparseable harness config makes managed_installed()
    return False, so a damaged file reads as unreachable rather than raising
    inside status().
    """
    registered = {
        "claude-code": _claude_registered(name, home=home),
        "codex": _codex_registered(name, home=home),
    }
    # Only the chosen harnesses need an entry; a deselected one is meant to be
    # absent, so counting it would report a problem the owner created on purpose.
    return [h for h in harness_choice(name, home=home) if not registered[h]]


def available_to_agents(name: str, *, home: Path | None = None) -> bool:
    """Whether every harness the owner chose can currently reach this connector.

    The policy flag is what Disable writes; the harness entries are what an
    agent resolves the connector through. Both must hold, because a missing
    entry means that agent cannot reach it however the flag reads.

    This is the panel's predicate, not run()'s. run() must not gate on the
    harness entries, for a reason that does not depend on how it was invoked:
    it cannot tell which harness is calling it, so requiring all chosen
    harnesses would let one harness's missing entry refuse a different harness
    that is correctly registered. The policy flag alone is the right gate there,
    and disable() sets it.

    Do not reach for "the entry must exist because the entry is what invokes
    it": `connector-run <name>` is an ordinary CLI subcommand, so it also runs
    from a hand-typed command or a stale entry a harness still has cached.
    """
    if not policy(name, home=home).get("enabled"):
        return False
    return not unreachable_harnesses(name, home=home)


def policy(name: str, *, home: Path | None = None) -> dict[str, object]:
    path = _policy_path(name)
    if path.exists():
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise RuntimeError("invalid connector policy")
        return value
    # Preserve legacy installed behavior visibly until the user changes it.
    installed = connector_dispatch.is_legacy_full() and (
        _claude_registered(name, home=home) or _codex_registered(name, home=home)
    )
    source = None
    if installed:
        source = "gh-cli" if _gh_cli_token() else "stored"
    return {
        "enabled": installed,
        "source": source,
        "identity": None,
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


_HARNESS_BINARIES = {"claude-code": "claude", "codex": "codex"}


def harness_present(harness: str) -> bool:
    """Whether the agent CLI exists here. A registration is written either way —
    it applies when the harness arrives — but the panel must not claim a machine
    without Codex is serving tools to Codex."""
    return shutil.which(_HARNESS_BINARIES[harness]) is not None


def harness_choice(name: str, *, home: Path | None = None) -> list[str]:
    """Harnesses this connector is registered for.

    Stored per connector so a choice survives disable/enable. A connector that
    predates the setting has no stored choice, so read the harness configs
    instead of assuming both — otherwise the row would claim Codex on a machine
    where only Claude Code was ever registered, which is the false checkmark
    this setting exists to remove. Nothing registered yet means both, the
    default for a fresh connect."""
    stored = policy(name, home=home).get("harnesses")
    if isinstance(stored, list):
        chosen = [h for h in HARNESSES if h in stored]
        return chosen or list(HARNESSES)
    registered = [
        harness
        for harness, installed in (
            ("claude-code", _claude_registered(name, home=home)),
            ("codex", _codex_registered(name, home=home)),
        )
        if installed
    ]
    return registered or list(HARNESSES)


def _install(name: str, *, home: Path | None = None, harnesses: list[str] | None = None) -> None:
    connector_dispatch.register_runtime(_duckterm_bin())
    wanted = harnesses if harnesses is not None else harness_choice(name, home=home)
    for harness in HARNESSES:
        if harness in wanted:
            mcp_install.managed_install(name, harness, home=home)
        else:
            mcp_install.managed_remove(name, harness, home=home)


def refresh_runtime_registration() -> None:
    """Refresh only this profile's executable and existing managed entries.

    Called after the server has established home ownership. No credentials are
    loaded, no connector is enabled and no foreign membership is claimed.
    """
    if os.environ.get("DUCKTERM_HOSTED"):
        return  # Hosted harness registrations remain administrator-managed.
    connector_dispatch.register_runtime(_duckterm_bin())
    for name in NAMES:
        for harness in HARNESSES:
            if mcp_install.managed_installed(name, harness):
                mcp_install.managed_install(name, harness)


def _duckterm_bin() -> str:
    """Absolute path to the duckterm CLI for MCP entries — harnesses spawn MCP
    commands without a login shell, so PATH lookup is not reliable there. The
    pipx shim comes first: it survives upgrades and repo moves, while a dev
    venv's path dies with its checkout (see RETRO.md, hooks-127)."""
    # The native app supplies a CLI matching its backend, independent of pipx.
    bundled = os.environ.get("DUCKTERM_BUNDLED_CLI")
    if bundled:
        if Path(bundled).is_file() and os.access(bundled, os.X_OK):
            return bundled
        raise RuntimeError("This edition's app moved or was removed; reopen it before connecting")
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
    *,
    home: Path | None = None,
    source: str | None = None,
    harnesses: list[str] | None = None,
) -> dict[str, object]:
    previous = policy(name, home=home)
    if harnesses is not None:
        unknown = [h for h in harnesses if h not in HARNESSES]
        if unknown:
            raise RuntimeError(f"Unknown harness {unknown[0]!r}")
        if not harnesses:
            raise RuntimeError("Choose at least one agent for this connector")
    chosen = list(harnesses) if harnesses is not None else harness_choice(name, home=home)
    from duckterm import connector_client

    if os.environ.get("DUCKTERM_HOSTED"):
        raise RuntimeError("Manage hosted credentials through the separate connector administrator")
    if connector_client.configured():
        if token:
            raise RuntimeError("Configure provider credentials on the shared connector host")
        available = {row["name"]: row for row in connector_client.statuses()}
        if not available.get(name, {}).get("enabled"):
            raise RuntimeError("Connector is not enabled for this workspace on the shared host")
        _install(name, home=home, harnesses=chosen)
        _save_policy(name, {"enabled": True, "harnesses": chosen, "generation": uuid.uuid4().hex})
        return status(name, home=home)
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
    # Publish disabled first: an old live runner must not use a replaced identity.
    _save_policy(name, {**previous, "enabled": False, "generation": uuid.uuid4().hex})
    try:
        _install(name, home=home, harnesses=chosen)
    except Exception:
        mcp_install.managed_remove(name, "claude-code", home=home)
        mcp_install.managed_remove(name, "codex", home=home)
        raise
    _save_policy(
        name,
        {
            "enabled": True,
            "source": source,
            "identity": identity,
            "harnesses": chosen,
            "generation": uuid.uuid4().hex,
        },
    )
    return status(name, home=home)


def disable(name: str, *, home: Path | None = None) -> dict[str, object]:
    current = policy(name, home=home)
    if os.environ.get("DUCKTERM_HOSTED"):
        raise RuntimeError("Disable hosted connectors through the connector administrator")
    _save_policy(name, {**current, "enabled": False, "generation": uuid.uuid4().hex})
    mcp_install.managed_remove(name, "claude-code", home=home)
    mcp_install.managed_remove(name, "codex", home=home)
    return status(name, home=home)


def forget(name: str, *, home: Path | None = None) -> dict[str, object]:
    disable(name, home=home)
    delete_secret(name)
    _save_policy(
        name,
        {
            "enabled": False,
            "source": None,
            "identity": None,
            "generation": uuid.uuid4().hex,
        },
    )
    return status(name, home=home)


def status(name: str, *, home: Path | None = None) -> dict[str, object]:
    from duckterm import connector_client

    if connector_client.configured():
        return next(row for row in list_status(home=home) if row["name"] == name)
    current = policy(name, home=home)
    installed = {
        "claude-code": _claude_registered(name, home=home),
        "codex": _codex_registered(name, home=home),
    }
    chosen = harness_choice(name, home=home)
    source = current.get("source")
    detail = None
    available: list[str] = []
    runner_ready = True
    if shutil.which("gh"):
        available.append("gh-cli")
    available.append("stored")
    try:
        github_server_argv()
    except RuntimeError:
        # No server binary and no Docker: agents would get nothing from a valid
        # token, so say it here rather than let Connect look like it worked.
        runner_ready = False
        detail = "install github-mcp-server or Docker so agents can run it"
    unreachable = unreachable_harnesses(name, home=home)
    return {
        "name": name,
        "title": "GitHub",
        "description": "Repos, PRs, issues, and actions",
        "credential": source,
        "identity": current.get("identity"),
        "sources": available,
        "installed": installed,
        "harnesses": chosen,
        "harnesses_present": {h: harness_present(h) for h in HARNESSES},
        "enabled": available_to_agents(name, home=home),
        # The two doors, reported separately. `enabled` above is their AND, kept
        # for callers that only ask "can an agent use this"; these two say which
        # door is shut, which one boolean cannot. Duckterm's own features need a
        # credential and nothing else -- Disable is about agents.
        "duckterm_uses": bool(source),
        "agents_use": available_to_agents(name, home=home),
        "agents_off_by_choice": not current.get("enabled"),
        "unreachable_harnesses": unreachable,
        "runner_ready": runner_ready,
        "ready": runner_ready and source is not None,
        "detail": detail,
        "managed": bool(os.environ.get("DUCKTERM_HOSTED")),
        "revoke_url": (
            "https://github.com/settings/applications"
            if source == "gh-cli"
            else "https://github.com/settings/tokens"
        ),
    }


def list_status(*, home: Path | None = None) -> list[dict[str, object]]:
    from duckterm import connector_client

    if not connector_client.configured():
        return [status(n, home=home) for n in NAMES]
    try:
        remote = {row["name"]: row for row in connector_client.statuses()}
        detail = None
    except (OSError, ValueError, RuntimeError):
        remote, detail = {}, "Shared connector service unavailable"
    rows = []
    for name in NAMES:
        on_host = bool(remote.get(name, {}).get("enabled"))
        unreachable = unreachable_harnesses(name, home=home)
        rows.append(
            {
                "name": name,
                "title": "GitHub",
                "description": "Provided by your shared connector host",
                "credential": "shared-host" if on_host else None,
                "installed": {
                    "claude-code": _claude_registered(name, home=home),
                    "codex": _codex_registered(name, home=home),
                },
                "enabled": on_host and not unreachable,
                "duckterm_uses": on_host,
                "agents_use": on_host and not unreachable,
                "agents_off_by_choice": not on_host,
                "unreachable_harnesses": unreachable,
                "harnesses": harness_choice(name, home=home),
                "harnesses_present": {h: harness_present(h) for h in HARNESSES},
                "runner_ready": on_host,
                "ready": on_host,
                "managed": True,
                "hosted": bool(os.environ.get("DUCKTERM_HOSTED")),
                "sources": [],
                "identity": remote.get(name, {}).get("identity"),
                "revoke_url": "https://github.com/settings/tokens",
                "detail": detail or (None if on_host else "Not enabled for this workspace"),
            }
        )
    return rows


def server_command(
    name: str, current: dict[str, object] | None = None
) -> tuple[list[str], dict[str, str]]:
    current = current or policy(name)
    env = dict(os.environ)
    # Ambient credentials must not override the explicitly selected source/policy.
    for env_key in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN"):
        env.pop(env_key, None)
    cred = github_token(str(current.get("source")))
    if cred is None:
        raise RuntimeError("Selected GitHub credential is unavailable; reconnect it")
    env[_GITHUB_ENV] = cred[0]
    return github_server_argv(), env


class _RunnerPolicy:
    """Watch revocation without resolving credentials or reparsing unchanged files."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.home = Path.home()
        self.path = _policy_path(name)
        self.signature: tuple[object, ...] | None = None
        self.current: dict[str, object] = {}

    @staticmethod
    def _stamp(path: Path) -> tuple[int, ...] | None:
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def read(self) -> dict[str, object]:
        policy_stamp = self._stamp(self.path)
        signature: tuple[object, ...] = (policy_stamp,)
        if policy_stamp is None and connector_dispatch.is_legacy_full():
            from duckterm.agents import hook_dispatch

            signature += (
                self._stamp(mcp_install._claude_path(self.home)),
                self._stamp(mcp_install._codex_path(self.home)),
                self._stamp(hook_dispatch.shared_script_path().parent / "registrations.json"),
            )
        if signature == self.signature:
            return self.current
        current: dict[str, object]
        if policy_stamp is not None:
            current = json.loads(self.path.read_text())
            if not isinstance(current, dict):
                raise RuntimeError("invalid connector policy")
        else:
            # Existing children need only enabled/generation. In particular, do
            # not call policy(): its legacy branch runs `gh auth token`.
            current = {
                "enabled": connector_dispatch.is_legacy_full()
                and (
                    _claude_registered(self.name, home=self.home)
                    or _codex_registered(self.name, home=self.home)
                ),
                "generation": "legacy",
            }
        self.current = current
        # Keep the pre-read signature: a concurrent change is reread next tick.
        self.signature = signature
        return current


def run(name: str) -> None:
    from duckterm import connector_client

    if connector_client.configured():
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
        watcher = _RunnerPolicy(name)
        while proc.poll() is None:
            latest = watcher.read()
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


def verify(name: str, *, timeout: float = 30.0) -> dict[str, object]:
    """Speak MCP to the connector the way a harness does and report what it serves.

    Runs the registered command itself (`duckterm connector-run NAME`), so a
    pass means the harness path works — not that a config entry exists. stdin
    stays open until the reply arrives: closing it early makes servers exit
    ("server is closing: EOF") and look broken when they are fine.
    """
    if name not in NAMES:
        raise ValueError(f"unknown connector {name!r}")
    command = connector_dispatch.register_runtime(_duckterm_bin())
    argv = [str(command), "connector-run", name]
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={**os.environ, **instance.launch_env()},
    )
    replies: dict[int, dict[str, object]] = {}

    def collect() -> None:
        for line in proc.stdout or ():
            if not line.startswith("{"):
                continue
            with contextlib.suppress(ValueError):
                message = json.loads(line)
                if isinstance(message, dict) and isinstance(message.get("id"), int):
                    replies[message["id"]] = message

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()

    def send(message: dict[str, object]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def await_reply(request_id: int) -> dict[str, object] | None:
        while request_id not in replies and time.monotonic() < deadline:
            if proc.poll() is not None and request_id not in replies:
                return None
            time.sleep(0.05)
        return replies.get(request_id)

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "duckterm-verify", "version": "1"},
                },
            }
        )
        handshake = await_reply(1)
        if handshake is None:
            return _verify_failure(proc, "The connector did not start")
        # A server that rejects the handshake is unusable no matter what it
        # answers next, so this must be checked before tools/list is sent.
        if "error" in handshake:
            return _rpc_failure(handshake, "The connector rejected the connection")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listing = await_reply(2)
        if listing is None:
            return _verify_failure(proc, "The connector did not list its tools")
        if "error" in listing:
            return _rpc_failure(listing, "The connector could not list its tools")
        result = listing.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        # Only a real tool array counts. `len("invalid")` reported 7 tools.
        if not isinstance(tools, list):
            return {
                "ok": False,
                "detail": "The connector did not list its tools in a usable form",
                "tools": 0,
            }
        # An agent calls a tool by name, so an entry without a usable one is not
        # a tool the owner can rely on; counting it would overstate the listing.
        if not all(
            isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"].strip()
            for tool in tools
        ):
            return {
                "ok": False,
                "detail": "The connector listed a tool with no usable name",
                "tools": 0,
            }
        # The panel's claim is "you can use this", and a server offering nothing
        # to call cannot support it — report that rather than a verified zero.
        if not tools:
            return {"ok": False, "detail": "The connector offers no tools", "tools": 0}
        return {"ok": True, "detail": None, "tools": len(tools)}
    except OSError as exc:
        return {"ok": False, "detail": str(exc)[:200], "tools": 0}
    finally:
        with contextlib.suppress(OSError):
            if proc.stdin:
                proc.stdin.close()
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _rpc_failure(reply: dict[str, object], summary: str) -> dict[str, object]:
    """Prefer the server's own message — it names the version or scope at fault."""
    error = reply.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return {"ok": False, "detail": str(message or error or summary)[:200], "tools": 0}


def _verify_failure(proc: "subprocess.Popen[str]", summary: str) -> dict[str, object]:
    """Surface the server's own complaint — it names the missing binary or login."""
    with contextlib.suppress(OSError, ValueError):
        if proc.poll() is not None and proc.stderr:
            tail = (proc.stderr.read() or "").strip().splitlines()
            if tail:
                return {"ok": False, "detail": f"{summary}: {tail[-1][:160]}", "tools": 0}
    return {"ok": False, "detail": summary, "tools": 0}


def execution_state(name: str) -> dict[str, object]:
    return policy(name)


def _set_enabled(name: str, enabled: bool) -> None:
    """Compatibility entry point for shared-host policy administration."""
    _save_policy(name, {**policy(name), "enabled": enabled, "generation": uuid.uuid4().hex})
