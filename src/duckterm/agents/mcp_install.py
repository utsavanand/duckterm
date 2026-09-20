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
    path.write_text(json.dumps(config, indent=2) + "\n")
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
    path.write_text(json.dumps(config, indent=2) + "\n")
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
    path.write_text(candidate)
    return path


def codex_remove(name: str, *, home: Path | None = None) -> bool:
    path = _codex_path(home or Path.home())
    if not path.exists() or not codex_installed(name, home=home):
        return False
    candidate = _strip_codex_sections(path.read_text(), name)
    tomllib.loads(candidate)
    path.write_text(candidate)
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
