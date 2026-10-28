import json
from pathlib import Path

from duckterm.agents.hooks_install import (
    _EVENTS,
    install,
    settings_path,
    uninstall,
)


def read_settings(project: Path) -> dict:
    return json.loads((project / ".claude" / "settings.json").read_text())


def test_install_creates_settings_with_all_events(tmp_path: Path) -> None:
    install(global_scope=False, project_dir=tmp_path)
    settings = read_settings(tmp_path)
    assert set(settings["hooks"]) == set(_EVENTS)
    # Each event runs the duckterm hook with its event type + runtime as args.
    cmd = settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert "duckterm-hook.sh" in cmd
    assert cmd.endswith(" PostToolUse claude-code")


def test_install_is_idempotent(tmp_path: Path) -> None:
    install(global_scope=False, project_dir=tmp_path)
    install(global_scope=False, project_dir=tmp_path)
    settings = read_settings(tmp_path)
    # Running twice must not duplicate the entry.
    assert len(settings["hooks"]["Stop"]) == 1


def test_install_preserves_existing_user_hooks(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir()
    existing = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Edit", "hooks": [{"type": "command", "command": "my-linter.sh"}]}
            ]
        }
    }
    (claude / "settings.json").write_text(json.dumps(existing))

    install(global_scope=False, project_dir=tmp_path)
    settings = read_settings(tmp_path)

    commands = [h["command"] for e in settings["hooks"]["PostToolUse"] for h in e["hooks"]]
    assert "my-linter.sh" in commands  # the user's hook survives
    assert any("duckterm-hook.sh" in c for c in commands)  # ours is added


def test_uninstall_removes_only_duckterm_hooks(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir()
    existing = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Edit", "hooks": [{"type": "command", "command": "my-linter.sh"}]}
            ]
        }
    }
    (claude / "settings.json").write_text(json.dumps(existing))

    install(global_scope=False, project_dir=tmp_path)
    uninstall(global_scope=False, project_dir=tmp_path)
    settings = read_settings(tmp_path)

    commands = [h["command"] for e in settings["hooks"]["PostToolUse"] for h in e["hooks"]]
    assert commands == ["my-linter.sh"]  # user's hook intact, ours gone
    # Events that had only duckterm hooks are removed entirely.
    assert "Stop" not in settings.get("hooks", {})


def test_global_scope_targets_home(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = settings_path(global_scope=True, project_dir=Path("/ignored"))
    assert path == tmp_path / ".claude" / "settings.json"


def test_codex_uses_hooks_json_with_claude_style_shape(tmp_path: Path) -> None:
    path = install(global_scope=False, project_dir=tmp_path, agent="codex")
    assert path == tmp_path / ".codex" / "hooks.json"
    config = json.loads(path.read_text())
    assert set(config["hooks"]) == set(_EVENTS)
    cmd = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    # Codex shares Claude's event names; runtime is tagged as codex.
    assert cmd.endswith(" PreToolUse codex")


def test_copilot_uses_camelcase_events_and_runtime(tmp_path: Path) -> None:
    path = install(global_scope=False, project_dir=tmp_path, agent="copilot")
    assert path == tmp_path / ".github" / "hooks" / "duckterm.json"
    config = json.loads(path.read_text())
    assert config["version"] == 1
    # Copilot's event keys are camelCase, but the canonical name is passed to
    # the script so the server sees one vocabulary.
    assert "sessionStart" in config["hooks"]
    cmd = config["hooks"]["preToolUse"][0]["command"]
    assert cmd.endswith(" PreToolUse copilot")


def test_uninstall_is_per_agent(tmp_path: Path) -> None:
    install(global_scope=False, project_dir=tmp_path, agent="codex")
    uninstall(global_scope=False, project_dir=tmp_path, agent="codex")
    config = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    # All duckterm entries gone; empty hooks block removed.
    assert config.get("hooks", {}) == {}


def test_remove_keeps_custom_children_in_same_group(tmp_path: Path) -> None:
    path = install(global_scope=False, project_dir=tmp_path)
    config = json.loads(path.read_text())
    custom = {"type": "command", "command": "echo duckterm", "timeout": 7}
    config["hooks"]["Stop"][0]["hooks"].append(custom)
    config["hooks"]["Stop"][0]["custom"] = "retained"
    path.write_text(json.dumps(config))
    uninstall(global_scope=False, project_dir=tmp_path)
    assert json.loads(path.read_text())["hooks"] == {
        "Stop": [{"matcher": "*", "hooks": [custom], "custom": "retained"}]
    }


def test_reinstall_keeps_custom_children_in_same_group(tmp_path: Path) -> None:
    path = install(global_scope=False, project_dir=tmp_path)
    config = json.loads(path.read_text())
    config["hooks"]["Stop"][0]["hooks"].append({"command": "my-linter"})
    path.write_text(json.dumps(config))
    install(global_scope=False, project_dir=tmp_path)
    commands = [
        h["command"] for e in json.loads(path.read_text())["hooks"]["Stop"] for h in e["hooks"]
    ]
    assert "my-linter" in commands
    assert len(commands) == 2


def test_uninstall_does_not_rewrite_unrelated_config(tmp_path: Path) -> None:
    path = settings_path(global_scope=False, project_dir=tmp_path)
    path.parent.mkdir()
    original = '{ "hooks": {"Stop": [{"hooks": [{"command": "echo duckterm"}]}]}, "other": 1 }\n'
    path.write_text(original)
    uninstall(global_scope=False, project_dir=tmp_path)
    assert path.read_text() == original


def test_uninstall_refuses_symlink(tmp_path: Path) -> None:
    import pytest

    path = install(global_scope=False, project_dir=tmp_path)
    original = path.read_bytes()
    target = tmp_path / "custom.json"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="unsafe"):
        uninstall(global_scope=False, project_dir=tmp_path)
    assert target.read_bytes() == original


def test_copilot_preserves_custom_alternative_command(tmp_path: Path) -> None:
    import pytest

    path = install(global_scope=False, project_dir=tmp_path, agent="copilot")
    config = json.loads(path.read_text())
    config["hooks"]["sessionStart"][0]["bash"] = "custom-tool"
    original = json.dumps(config)
    path.write_text(original)
    with pytest.raises(ValueError, match="Mixed"):
        uninstall(global_scope=False, project_dir=tmp_path, agent="copilot")
    assert path.read_text() == original


def test_ownership_does_not_match_mentions_or_shell_wrappers() -> None:
    from duckterm.agents.hooks_install import _is_ours, hook_script_path

    assert _is_ours(f'"{hook_script_path()}" Stop claude-code')
    for value in [
        "echo duckterm",
        "/custom/duckterm-linter.sh",
        "echo /x/duckterm-hook.sh Stop codex",
        '"unterminated',
        '"/x/duckterm-hook.sh" Stop codex; other',
        None,
    ]:
        assert not _is_ours(value)


def test_no_owned_hooks_keeps_empty_shared_fields(tmp_path: Path) -> None:
    path = settings_path(global_scope=False, project_dir=tmp_path)
    path.parent.mkdir()
    original = '{ "hooks": {}, "version": 1 }\n'
    path.write_text(original)
    uninstall(global_scope=False, project_dir=tmp_path)
    assert path.read_text() == original


def test_custom_same_basename_is_preserved(tmp_path: Path) -> None:
    path = install(global_scope=False, project_dir=tmp_path)
    config = json.loads(path.read_text())
    custom = {"type": "command", "command": "/custom/user-tools/duckterm-hook.sh Stop claude-code"}
    config["hooks"]["Stop"][0]["hooks"].append(custom)
    path.write_text(json.dumps(config))
    uninstall(global_scope=False, project_dir=tmp_path)
    assert json.loads(path.read_text())["hooks"]["Stop"][0]["hooks"] == [custom]


def test_deleted_known_pipx_package_script_remains_recognizable(
    tmp_path: Path, monkeypatch
) -> None:
    from duckterm.agents.hooks_install import _is_ours

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    script = (
        tmp_path
        / ".local/pipx/venvs/duckterm/lib/python3.13/site-packages/duckterm/hooks/duckterm-hook.sh"
    )
    assert not script.exists()
    assert _is_ours(f'"{script}" Stop claude-code')
    assert not _is_ours(f'"{script.parent / ".." / "duckterm-hook.sh"}" Stop claude-code')


APP_SCRIPT = (
    "Contents/Resources/python/lib/python3.13/site-packages/duckterm/hooks/duckterm-hook.sh"
)


def test_hooks_from_another_app_location_are_replaced_not_duplicated(tmp_path: Path) -> None:
    from duckterm.agents.hooks_install import claude_style_build

    full = f"/Applications/DuckTerm.app/{APP_SCRIPT}"
    lite = f"/Users/me/Downloads/Dir With Spaces/DuckTerm Lite.app/{APP_SCRIPT}"
    config = claude_style_build({}, full, "claude-code")
    config = claude_style_build(config, lite, "claude-code")
    for event in _EVENTS:
        commands = [h["command"] for e in config["hooks"][event] for h in e["hooks"]]
        assert commands == [f'"{lite}" {event} claude-code']


def test_uninstall_removes_hooks_left_by_a_deleted_app(tmp_path: Path) -> None:
    path = settings_path(global_scope=False, project_dir=tmp_path)
    path.parent.mkdir()
    gone = f"/Applications/Old Copy/DuckTerm.app/{APP_SCRIPT}"
    custom = {"type": "command", "command": "/custom/user-tools/duckterm-hook.sh Stop claude-code"}
    stale = {"type": "command", "command": f'"{gone}" Stop claude-code'}
    config = {
        "hooks": {
            "Stop": [
                {"matcher": "*", "hooks": [stale]},
                {"matcher": "*", "hooks": [custom]},
            ]
        }
    }
    path.write_text(json.dumps(config))
    uninstall(global_scope=False, project_dir=tmp_path)
    assert json.loads(path.read_text())["hooks"]["Stop"] == [{"matcher": "*", "hooks": [custom]}]


def test_app_bundle_ownership_requires_the_exact_package_layout() -> None:
    from duckterm.agents.hooks_install import _is_ours

    assert _is_ours(f'"/Applications/DuckTerm Code.app/{APP_SCRIPT}" PostToolUse codex')
    for value in [
        f'"/Applications/DuckTerm.app/{APP_SCRIPT}" PostToolUse unknown-runtime',
        f'"/Applications/DuckTerm.app/{APP_SCRIPT}" Stop claude-code extra',
        f'"Applications/DuckTerm.app/{APP_SCRIPT}" Stop claude-code',
        f'"/Applications/DuckTerm.app/../Other.app/{APP_SCRIPT}" Stop claude-code',
        '"/Applications/DuckTerm.app/Contents/Resources/duckterm-hook.sh" Stop claude-code',
        '"/Applications/Tool.app/Contents/Resources/python/lib/python3.13'
        '/site-packages/other/hooks/duckterm-hook.sh" Stop claude-code',
        '"/opt/DuckTerm/Contents/Resources/python/lib/python3.13'
        '/site-packages/duckterm/hooks/duckterm-hook.sh" Stop claude-code',
    ]:
        assert not _is_ours(value), value
