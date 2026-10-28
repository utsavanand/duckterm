"""Install/remove MCP server entries in each harness's user-level config.

  claude-code: ~/.claude.json          {"mcpServers": {name: {command, args, env}}}
  codex:       ~/.codex/config.toml    [mcp_servers.<name>] command/args (+ .env)

Same philosophy as hooks_install: touch only the entries we own (matched by
name), leave everything else in the file exactly as the user wrote it. The
codex config is TOML, which the stdlib can read but not write — so codex edits
are textual (drop our sections, append fresh ones) and the result is parsed
with tomllib before writing: a config we'd corrupt is a config we refuse to
touch.
"""

import json
import tomllib
from pathlib import Path
from typing import Any

from duckterm.helpers.private_files import private_write


def _claude_path(home: Path) -> Path:
    return home / ".claude.json"


def _codex_path(home: Path) -> Path:
    return home / ".codex" / "config.toml"


# ── claude-code (JSON) ──


def claude_install(
    name: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    path = _claude_path(home or Path.home())
    config = json.loads(path.read_text()) if path.exists() else {}
    servers = config.setdefault("mcpServers", {})
    entry: dict[str, object] = {"command": command, "args": args}
    if env:
        entry["env"] = env
    servers[name] = entry
    private_write(path, json.dumps(config, indent=2) + "\n")
    return path


def claude_remove(name: str, *, home: Path | None = None) -> bool:
    path = _claude_path(home or Path.home())
    if not path.exists():
        return False
    config = json.loads(path.read_text())
    servers = config.get("mcpServers", {})
    if name not in servers:
        return False
    del servers[name]
    private_write(path, json.dumps(config, indent=2) + "\n")
    return True


def claude_installed(name: str, *, home: Path | None = None) -> bool:
    path = _claude_path(home or Path.home())
    if not path.exists():
        return False
    try:
        config = json.loads(path.read_text())
    except json.JSONDecodeError:
        return False
    return name in config.get("mcpServers", {})


# ── codex (TOML) ──


def _is_our_header(line: str, name: str) -> bool:
    s = line.strip()
    return s.startswith(f"[mcp_servers.{name}]") or s.startswith(f"[mcp_servers.{name}.")


def _strip_codex_sections(text: str, name: str) -> str:
    """Drop every [mcp_servers.<name>*] section, keep everything else verbatim."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        if line.strip().startswith("["):
            skipping = _is_our_header(line, name)
        if not skipping:
            out.append(line)
    result = "\n".join(out)
    return result + "\n" if result and not result.endswith("\n") else result


def _toml_str(value: str) -> str:
    return json.dumps(value)  # JSON string escaping is valid TOML string syntax


def codex_install(
    name: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    path = _codex_path(home or Path.home())
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _strip_codex_sections(path.read_text() if path.exists() else "", name)
    if text and not text.endswith("\n"):
        text += "\n"
    lines = [
        f"[mcp_servers.{name}]",
        f"command = {_toml_str(command)}",
        f"args = [{', '.join(_toml_str(a) for a in args)}]",
    ]
    if env:
        lines.append(f"[mcp_servers.{name}.env]")
        lines += [f"{k} = {_toml_str(v)}" for k, v in env.items()]
    candidate = text + "\n".join(lines) + "\n"
    tomllib.loads(candidate)  # refuse to write a config we'd corrupt
    private_write(path, candidate)
    return path


def codex_remove(name: str, *, home: Path | None = None) -> bool:
    path = _codex_path(home or Path.home())
    if not path.exists() or not codex_installed(name, home=home):
        return False
    candidate = _strip_codex_sections(path.read_text(), name)
    tomllib.loads(candidate)
    private_write(path, candidate)
    return True


def codex_installed(name: str, *, home: Path | None = None) -> bool:
    path = _codex_path(home or Path.home())
    if not path.exists():
        return False
    try:
        config = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError:
        return False
    return name in config.get("mcp_servers", {})


# Profile-owned Duckterm registrations. The generic writers above are retained
# for callers deliberately managing an arbitrary MCP entry; connectors use only
# the guarded operations below.


def _managed_path(harness: str, home: Path | None) -> Path:
    if harness not in {"claude-code", "codex"}:
        raise ValueError(f"Unknown connector harness: {harness}")
    # Resolve the trusted HOME prefix, never a configuration directory below
    # it. Membership must survive switching HOME to its canonical spelling.
    root = (home or Path.home()).resolve()
    return _claude_path(root) if harness == "claude-code" else _codex_path(root)


def _managed_config(raw: bytes | None, harness: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config = (
        (json.loads(raw) if harness == "claude-code" else tomllib.loads(raw.decode()))
        if raw
        else {}
    )
    key = "mcpServers" if harness == "claude-code" else "mcp_servers"
    if not isinstance(config, dict) or not isinstance(config.get(key, {}), dict):
        raise ValueError("Invalid MCP configuration; leaving it unchanged")
    return config, config.get(key, {})


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{_toml_str(k)} = {_toml_value(v)}" for k, v in value.items()) + " }"
        )
    raise ValueError("Unsupported custom MCP option; leaving configuration unchanged")


def _managed_text(
    raw: bytes | None, config: dict[str, Any], harness: str, name: str, entry: dict[str, Any] | None
) -> bytes:
    if harness == "claude-code":
        servers = config.setdefault("mcpServers", {})
        if entry is None:
            servers.pop(name, None)
        else:
            servers[name] = entry
        return (json.dumps(config, indent=2) + "\n").encode()
    candidate = _strip_codex_sections(raw.decode() if raw else "", name)
    if entry is not None:
        candidate += f"[mcp_servers.{name}]\n"
        candidate += "".join(f"{_toml_str(k)} = {_toml_value(v)}\n" for k, v in entry.items())
    parsed = tomllib.loads(candidate)
    # Alternate quoted/dotted table syntax that our narrow textual stripper
    # cannot safely edit is refused, never silently duplicated or removed.
    expected = dict(config)
    expected["mcp_servers"] = dict(config.get("mcp_servers", {}))
    if entry is None:
        expected["mcp_servers"].pop(name, None)
        if not expected["mcp_servers"] and "mcp_servers" not in parsed:
            expected.pop("mcp_servers")
    else:
        expected["mcp_servers"][name] = entry
    if parsed != expected:
        raise ValueError("Cannot safely edit this MCP table syntax; leaving it unchanged")
    return candidate.encode()


def managed_install(name: str, harness: str, *, home: Path | None = None) -> Path:
    from duckterm.agents import connector_dispatch, hook_dispatch
    from duckterm.agents.hook_files import _lock, _read, _write

    path = _managed_path(harness, home)
    runtime = f"mcp:{harness}:{name}"
    with (
        hook_dispatch.registrations(create=True) as registry,
        _lock(path, create=True) as directory,
    ):
        assert directory is not None
        raw = _read(path.name, directory)
        config, servers = _managed_config(raw, harness)
        old = servers.get(name)
        if old is not None and not connector_dispatch.owned_entry(old, name):
            raise ValueError(f"{name} already has a custom MCP definition; leaving it unchanged")
        legacy = old is not None and old.get("command") != str(connector_dispatch.launcher_path())
        members = registry.members(path, runtime, legacy=legacy)
        if legacy:
            assert old is not None
            connector_dispatch.preserve_legacy_runtime(old)
        entry = {
            **(old or {}),
            "command": str(connector_dispatch.launcher_path()),
            "args": ["connector-run", name],
        }
        if harness == "codex":
            inherited = entry.get("env_vars", [])
            if not isinstance(inherited, list) or not all(isinstance(v, str) for v in inherited):
                raise ValueError(
                    "Unsupported custom MCP environment forwarding; leaving it unchanged"
                )
            entry["env_vars"] = list(dict.fromkeys([*inherited, *connector_dispatch.CONTEXT]))
        candidate = _managed_text(raw, config, harness, name, entry)
        # Publish membership first: a crash must not leave a migrated shared
        # command with no record that the previous Full registration existed.
        registry.save(path, runtime, members | {hook_dispatch.profile_key()})
        _write(path.name, candidate, directory)
    return path


def managed_remove(name: str, harness: str, *, home: Path | None = None) -> bool:
    from duckterm.agents import connector_dispatch, hook_dispatch
    from duckterm.agents.hook_files import _lock, _read, _write

    path = _managed_path(harness, home)
    runtime = f"mcp:{harness}:{name}"
    with (
        hook_dispatch.registrations(create=False) as registry,
        _lock(path, create=False) as directory,
    ):
        if directory is None:
            return False
        raw = _read(path.name, directory)
        config, servers = _managed_config(raw, harness)
        old = servers.get(name)
        if not connector_dispatch.owned_entry(old, name):
            return False  # A custom definition, even with our name, is never ours.
        members = registry.members(
            path, runtime, legacy=old["command"] != str(connector_dispatch.launcher_path())
        )
        owner = hook_dispatch.profile_key()
        if owner not in members:
            return False
        if members == {owner}:
            _write(path.name, _managed_text(raw, config, harness, name, None), directory)
        registry.save(path, runtime, members - {owner})
    return True


def managed_installed(name: str, harness: str, *, home: Path | None = None) -> bool:
    from duckterm.agents import connector_dispatch, hook_dispatch

    path = _managed_path(harness, home)
    try:
        _, servers = _managed_config(path.read_bytes(), harness)
        entry = servers.get(name)
        if not connector_dispatch.owned_entry(entry, name):
            return False
        legacy = entry["command"] != str(connector_dispatch.launcher_path())
        return hook_dispatch.profile_key() in hook_dispatch.registered_profiles(
            path, f"mcp:{harness}:{name}", legacy=legacy
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False
