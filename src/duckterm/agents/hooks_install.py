"""Wire a coding agent so every session streams into Duckterm.

`install-hooks` merges Duckterm's hook entries into the agent's hook config,
tagged so install is idempotent and uninstall removes exactly what we added.
The same shipped hook script (`duckterm-hook.sh`) is reused for every agent:
each agent's hooks deliver the event JSON on stdin and pass the event type as
$1, and the script POSTs it to the server.

This module owns the JSON-merge logic shared across agents; each agent's own
adapter (in runtimes/) declares which build/strip it uses and where its config
lives, via a `HookSpec`. install/uninstall resolve that spec through the harness
registry, so there's no per-agent table here.

Hook config locations:
  - claude-code: ~/.claude/settings.json (or repo ./.claude/settings.json)
  - codex:       ~/.codex/hooks.json     (repo-local is unreliable upstream;
                 prefer --global — see openai/codex#17532)
  - copilot:     ~/.copilot/hooks/duckterm.json (or repo .github/hooks/)
"""

import json
import os
import re
import shlex
from collections.abc import Iterable
from importlib.resources import files
from pathlib import Path
from typing import Any

from duckterm.agents import hook_dispatch
from duckterm.core import events
from duckterm.runtimes.base import HookSpec

# Canonical event set (Claude's names), defined once in core.events. Each
# harness maps these to its own hook names below.
_EVENTS = events.ALL

# The pre-exec permission event must BLOCK so the hook can long-poll Duckterm
# for the user's decision and return it to the agent (the dashboard becomes the
# approval authority). Supported Stop hooks also return inbox context. Timeout must exceed
# the hook's poll cap (~180s) so the agent waits for a real answer.
_BLOCKING_EVENT = "PermissionRequest"
_BLOCKING_TIMEOUT = 200


def hook_script_path() -> Path:
    """Absolute path to the shipped hook script (works from an installed wheel)."""
    return Path(str(files("duckterm").joinpath("hooks/duckterm-hook.sh")))


def _hook_invocation(command: str) -> Path | None:
    if not isinstance(command, str):
        return None
    try:
        args = shlex.split(command)
    except ValueError:
        return None
    if (
        len(args) == 3
        and Path(args[0]).name == "duckterm-hook.sh"
        and args[1] in _EVENTS
        and args[2] in {"claude-code", "codex", "copilot"}
    ):
        return Path(args[0])
    return None


def _in_app_bundle(path: Path) -> bool:
    """The exact hook script location inside a DuckTerm app bundle, wherever the app is."""
    return (
        path.is_absolute()
        and ".." not in path.parts
        and re.fullmatch(
            r"/.+\.app/Contents/Resources/python/lib/python[0-9]+\.[0-9]+"
            r"/site-packages/duckterm/hooks/duckterm-hook\.sh",
            path.as_posix(),
        )
        is not None
    )


def _is_ours(command: str) -> bool:
    """Require our loaded script, the exact package path in a known pipx venv, or
    the exact package path inside a DuckTerm app bundle at any location."""
    path = _hook_invocation(command)
    if path is None or not path.is_absolute() or ".." in path.parts:
        return False
    if path in {hook_script_path(), hook_dispatch.shared_script_path()}:
        return True
    # A moved or renamed app, or another edition's app, left hooks at its old
    # bundle path; without this a reinstall would add a second set beside them.
    return _in_app_bundle(path) or _in_pipx_venv(path)


def _in_pipx_venv(path: Path) -> bool:
    """The exact hook script location inside a known pipx venv for duckterm."""
    roots = [Path.home() / ".local/pipx", Path.home() / ".local/share/pipx"]
    if os.environ.get("PIPX_HOME"):
        roots.append(Path(os.environ["PIPX_HOME"]))
    for root in roots:
        try:
            relative = path.relative_to(root / "venvs/duckterm")
        except ValueError:
            continue
        if re.fullmatch(
            r"lib/python[0-9]+\.[0-9]+/site-packages/duckterm/hooks/duckterm-hook\.sh",
            relative.as_posix(),
        ):
            return True
    return False


# ── claude-code & codex: identical {hooks: {Event: [{matcher, hooks:[…]}]}} ──


def claude_style_build(config: dict[str, Any], script: str, runtime: str) -> dict[str, Any]:
    # Codex shares the file shape but does NOT support the `async` key — it skips
    # any hook that has one ("async hooks are not supported yet"). So for codex we
    # omit `async` entirely (its hooks run synchronously, bounded by `timeout`);
    # codex also has no blocking-approval support, so there's no blocking event.
    from duckterm.harnesses import REGISTRY

    supports_notice = REGISTRY[runtime].turn_end_inbox_notice
    supports_async = runtime != "codex"
    claude_style_strip(config)
    hooks: dict[str, Any] = config.setdefault("hooks", {})
    for event in _EVENTS:
        entries = hooks.setdefault(event, [])
        blocking = supports_async and event == _BLOCKING_EVENT
        # codex caps hook timeouts at 3s and prints a "clamping" warning at
        # every session start for anything higher — write 3 so it's quiet.
        plain_timeout = 3 if runtime == "codex" else 5
        hook: dict[str, Any] = {
            "type": "command",
            "command": shlex.join([script, event, runtime]),
            "timeout": _BLOCKING_TIMEOUT if blocking else plain_timeout,
        }
        if supports_async:
            # The permission event blocks (waits for the dashboard's decision);
            # supported Stop hooks return inbox feedback synchronously.
            hook["async"] = not (blocking or (event == "Stop" and supports_notice))
        entries.append({"matcher": "*", "hooks": [hook]})
    return config


def _hook_groups(config: dict[str, Any]) -> dict[str, Any]:
    hooks = config.get("hooks", {})
    if not isinstance(hooks, dict) or any(not isinstance(v, list) for v in hooks.values()):
        raise ValueError("Invalid hooks configuration; leaving it unchanged")
    return hooks


def claude_style_strip(config: dict[str, Any]) -> dict[str, Any]:
    hooks = _hook_groups(config)
    had_events = bool(hooks)
    for event, entries in list(hooks.items()):
        kept = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks", []), list):
                raise ValueError("Invalid hook group; leaving configuration unchanged")
            children = entry.get("hooks", [])
            remaining = [
                h for h in children if not (isinstance(h, dict) and _is_ours(h.get("command", "")))
            ]
            if len(remaining) == len(children):
                kept.append(entry)
            elif remaining:
                kept.append({**entry, "hooks": remaining})
        if kept or not entries:
            hooks[event] = kept
        else:
            del hooks[event]
    if had_events and not hooks:
        config.pop("hooks", None)
    return config


# ── copilot: {version, hooks: {camelEvent: [{type, bash, command}]}} ──

# Copilot uses camelCase event names; map our canonical names to theirs. We pass
# the CANONICAL name as $1 to the script so the server sees one vocabulary.
_COPILOT_EVENTS = {
    "SessionStart": "sessionStart",
    "UserPromptSubmit": "userPromptSubmitted",
    "PreToolUse": "preToolUse",
    "PostToolUse": "postToolUse",
    "PostToolUseFailure": "postToolUseFailure",
    "PermissionRequest": "permissionRequest",
    "Notification": "notification",
    "Stop": "agentStop",
    "SessionEnd": "sessionEnd",
}


def copilot_build(config: dict[str, Any], script: str, runtime: str) -> dict[str, Any]:
    copilot_strip(config)
    config.setdefault("version", 1)
    hooks: dict[str, Any] = config.setdefault("hooks", {})
    for canonical, cop_event in _COPILOT_EVENTS.items():
        entries = hooks.setdefault(cop_event, [])
        entries.append(
            {
                "type": "command",
                # `command` is Copilot's cross-platform fallback; pass the
                # canonical event name (so the server's vocabulary is uniform)
                # and the runtime so events are attributed to copilot.
                "command": shlex.join([script, canonical, runtime]),
                "timeoutSec": 5,
            }
        )
    return config


def copilot_strip(config: dict[str, Any]) -> dict[str, Any]:
    hooks = _hook_groups(config)
    had_events = bool(hooks)
    for event, entries in list(hooks.items()):
        kept = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid hook entry; leaving configuration unchanged")
            commands = [entry[k] for k in ("command", "bash", "powershell") if entry.get(k)]
            owned = [_is_ours(command) for command in commands]
            if any(owned) and not all(owned):
                raise ValueError("Mixed owned and custom hook commands; inspect before removal")
            if not any(owned):
                kept.append(entry)
        if kept or not entries:
            hooks[event] = kept
        else:
            del hooks[event]
    if had_events and not hooks:
        config.pop("hooks", None)
    # The format version is not a DuckTerm-owned hook.
    return config


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise ValueError("Hook configuration must be an object")
    return config


def _spec(agent: str) -> tuple[str, HookSpec]:
    from duckterm.harnesses import REGISTRY

    runtime = REGISTRY[agent]
    if runtime.hook_spec is None:
        raise ValueError(f"agent {agent!r} has no hook system to install")
    return runtime.name, runtime.hook_spec


def settings_path(*, global_scope: bool, project_dir: Path, agent: str = "claude-code") -> Path:
    _, spec = _spec(agent)
    return spec.path(global_scope=global_scope, project_dir=project_dir)


def install(*, global_scope: bool, project_dir: Path, agent: str = "claude-code") -> Path:
    from duckterm.agents.hook_files import _lock, _read, _write, install_file

    name, spec = _spec(agent)
    path = spec.path(global_scope=global_scope, project_dir=project_dir)
    owner = hook_dispatch.profile_key()
    with hook_dispatch.registrations(create=True) as registry:
        if spec.transport == "file-drop":
            if spec.payload is None:
                raise ValueError("file-drop hook has no payload")
            members = registry.members(path, name, legacy=path.exists())
            install_file(path, spec.payload())
        else:
            if spec.build is None:
                raise ValueError("config-entry hook has no builder")
            with _lock(path, create=True) as directory:
                assert directory is not None
                raw = _read(path.name, directory)
                config = json.loads(raw) if raw is not None else {}
                if not isinstance(config, dict):
                    raise ValueError("Hook configuration must be an object")
                members = registry.members(path, name, legacy=_has_owned_hooks(config))
                script = hook_dispatch.ensure_shared_script(hook_script_path())
                result = spec.build(config, str(script), name)
                _write(path.name, (json.dumps(result, indent=2) + "\n").encode(), directory)
        registry.save(path, name, members | {owner})
    return path


def _has_owned_hooks(config: dict[str, Any]) -> bool:
    def visit(entry: Any) -> bool:
        if not isinstance(entry, dict):
            return False
        if any(_is_ours(entry.get(key, "")) for key in ("command", "bash", "powershell")):
            return True
        children = entry.get("hooks", [])
        return isinstance(children, list) and any(visit(child) for child in children)

    return any(visit(entry) for entries in _hook_groups(config).values() for entry in entries)


def _repoint(config: dict[str, Any], script: Path) -> bool:
    """Rewrite managed hook commands (another app bundle, or a known pipx venv) to
    `script`, in place. Event, runtime, ordering and every other field stay as
    registered."""
    hooks = _hook_groups(config)
    changed = False

    def visit(entry: Any) -> None:
        nonlocal changed
        if not isinstance(entry, dict):
            return
        for key in ("command", "bash", "powershell"):
            command = entry.get(key)
            if not isinstance(command, str):
                continue
            path = _hook_invocation(command)
            if path is None or path == script:
                continue
            if not _is_ours(command):
                continue
            _, event, runtime = shlex.split(command)
            # A relocated app's name may hold $, ` or quotes; keep the argv as data.
            entry[key] = shlex.join([str(script), event, runtime])
            changed = True
        for child in entry.get("hooks", []) if isinstance(entry.get("hooks"), list) else []:
            visit(child)

    for entries in hooks.values():
        for entry in entries:
            visit(entry)
    return changed


def repair_moved_app_hooks(project_dirs: Iterable[Path] = ()) -> list[Path]:
    """Repair already-registered global hooks to the stable shared v1 handler.

    Production apps may repair hooks left by any old app, without changing the
    legacy Full destination for unscoped agents or enrolling a new profile.
    Test/custom instances and pipx startups never touch agent configuration.
    """
    from duckterm.agents.hook_files import _lock, _read, _write
    from duckterm.harnesses import REGISTRY
    from duckterm.helpers import instance

    source = hook_script_path()
    if not _in_app_bundle(source):
        return []
    profile = instance.configured_profile()
    if profile is not None:
        if profile["profile_id"] not in {"full", "lite", "code"}:
            return []
        instance.validate_desktop_context(profile["edition"])
    elif instance.instance_id() is not None or instance.home() != Path.home() / ".duckterm":
        return []
    repaired = []
    known_projects = tuple(project_dirs)
    for runtime in REGISTRY.values():
        spec = runtime.hook_spec
        if spec is None or spec.transport != "config-entry":
            continue
        paths = [spec.path(global_scope=True, project_dir=Path.home())]
        paths.extend(
            spec.path(global_scope=False, project_dir=project) for project in known_projects
        )
        for path in dict.fromkeys(paths):
            if not path.is_file():
                continue  # never leave a lock beside a missing configuration
            with (
                hook_dispatch.registrations(create=True) as registry,
                _lock(path, create=False) as directory,
            ):
                if directory is None:
                    continue
                raw = _read(path.name, directory)
                if raw is None:
                    continue
                config = json.loads(raw)
                if not isinstance(config, dict) or not _has_owned_hooks(config):
                    continue
                members = registry.members(path, runtime.name, legacy=True)
                script = hook_dispatch.ensure_shared_script(source)
                if _repoint(config, script):
                    _write(path.name, (json.dumps(config, indent=2) + "\n").encode(), directory)
                    repaired.append(path)
                registry.save(path, runtime.name, members)
    return repaired


def uninstall(*, global_scope: bool, project_dir: Path, agent: str = "claude-code") -> Path:
    name, spec = _spec(agent)
    path = spec.path(global_scope=global_scope, project_dir=project_dir)
    # Reuse the existing descriptor-based lock/write implementation. This refuses
    # symlink parents and replaces the file atomically without truncating it.
    from duckterm.agents.hook_files import _lock, _read, _write, uninstall_file

    owner = hook_dispatch.profile_key()
    with hook_dispatch.registrations(create=False) as registry:
        if spec.transport == "file-drop":
            members = registry.members(path, name, legacy=path.exists())
            if owner not in members:
                return path
            if members == {owner}:
                uninstall_file(path)
        else:
            if spec.strip is None:
                raise ValueError("config-entry hook has no stripper")
            with _lock(path, create=False) as directory:
                if directory is None:
                    return path
                raw = _read(path.name, directory)
                if raw is None:
                    return path
                config = json.loads(raw)
                if not isinstance(config, dict):
                    raise ValueError("Hook configuration must be an object")
                members = registry.members(path, name, legacy=_has_owned_hooks(config))
                if owner not in members:
                    return path
                if members == {owner}:
                    original = json.dumps(config, sort_keys=True)
                    result = spec.strip(config)
                    if json.dumps(result, sort_keys=True) != original:
                        _write(path.name, (json.dumps(result, indent=2) + "\n").encode(), directory)
        registry.save(path, name, members - {owner})
    return path
