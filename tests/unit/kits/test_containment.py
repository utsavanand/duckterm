"""Nothing a kit or a repository's install record names may reach outside the
project, and nothing a kit ships may come from outside the kit. Each test is
one of security's proofs against #420 at 48323fdc, turned negative."""

import json
import os

import pytest
from test_skill_kits import _kit, _tiny_kit

from duckterm.kits import install, plan, validate

MANIFEST = """
manifest_version = 1
name = "probe"
description = "containment probes"
version = "1.0.0"

[[components]]
kind = "knowledge"
path = "knowledge/notes.md"
{extra}
"""


def _install(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    install.apply(plan.build(_kit(), project, ["claude-code"]))
    return project


def _record(project):
    return json.loads((project / plan.RECORD).read_text())


def _forge(project, entries):
    record = _record(project)
    record["kits"]["sample-kit"]["entries"] = entries
    (project / plan.RECORD).write_text(json.dumps(record))


@pytest.mark.parametrize(
    "entry",
    [
        {"path": "../victim/precious.txt", "how": "file", "sha256": "0" * 64},
        {
            "path": "/tmp/anywhere/settings.json",
            "how": "json",
            "lists": [],
            "keys": [],
            "replaced": [],
        },
        {"path": "link/settings.json", "how": "toml-block", "begin": "a", "end": "b"},
        {"path": "AGENTS.md", "how": "shell"},
        "not a dict",
    ],
)
def test_record_entries_outside_the_project_are_refused_on_reinstall_and_uninstall(tmp_path, entry):
    project = _install(tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious.txt").write_text("keep me\n")
    (project / "link").symlink_to(victim)  # a symlinked dir may not escape either
    _forge(project, [entry])

    with pytest.raises(install.InstallRefused):
        install.apply(plan.build(_kit(), project, ["claude-code"]))
    with pytest.raises(install.InstallRefused):
        install.uninstall(project, "sample-kit")
    (report,) = install.verify(project)
    assert report.drifted and not report.ok()
    assert (victim / "precious.txt").read_text() == "keep me\n"
    assert not (tmp_path / "anywhere").exists()


def test_reinstall_keeps_the_hash_check_so_a_forged_entry_cannot_delete_an_edited_file(tmp_path):
    project = _install(tmp_path)
    skill = project / ".claude" / "skills" / "raise-pr" / "SKILL.md"
    skill.write_text("owner rewrote this\n")
    with pytest.raises(install.InstallRefused, match="edited since this kit wrote it"):
        install.apply(plan.build(_kit(), project, ["claude-code"]))
    assert skill.read_text() == "owner rewrote this\n"
    install.apply(plan.build(_kit(), project, ["claude-code"]), replace=True)
    assert skill.read_text() != "owner rewrote this\n"


def test_component_names_that_are_paths_fail_validation(tmp_path):
    kit = _tiny_kit(
        tmp_path,
        MANIFEST.format(
            extra="""
name = "../../../victim/KNOWLEDGE_ESCAPED"

[[components]]
kind = "skill"
path = "skills/judge"
only = ["claude-code"]
replaces = "../../../victim/SKILL_ESCAPED"
"""
        ),
        {"knowledge/notes.md": "notes\n", "skills/judge/SKILL.md": "---\nname: judge\n---\nx\n"},
    )
    messages = [str(f) for f in validate.validate(kit)]
    assert any("KNOWLEDGE_ESCAPED' must match [a-z0-9_-]" in m for m in messages)
    assert any("SKILL_ESCAPED' must be a skill name" in m for m in messages)
    with pytest.raises(install.InstallRefused):
        install.apply(plan.build(kit, tmp_path / "proj", ["claude-code"]))
    assert not (tmp_path / "victim").exists()


def test_symlinked_sources_fail_validation_and_are_never_copied(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET\n")
    (outside / "hook.sh").write_text("#!/bin/sh\n")
    kit = _tiny_kit(
        tmp_path,
        MANIFEST.format(
            extra="""
[[components]]
kind = "skill"
path = "skills/secret"

[[components]]
kind = "hook"
event = "stop"
command = "hooks/run.sh"
"""
        ),
        {"knowledge/notes.md": "notes\n", "skills/.keep": ""},
    )
    (kit.root / "skills" / "secret").symlink_to(outside)
    (kit.root / "hooks").mkdir()
    (kit.root / "hooks" / "run.sh").symlink_to(outside / "hook.sh")
    messages = [str(f) for f in validate.validate(kit)]
    assert any("is a symlink at 'skills/secret'" in m for m in messages)
    assert any("is a symlink at 'hooks/run.sh'" in m for m in messages)

    for p in plan.build(kit, tmp_path / "proj", ["claude-code"]).placements:
        for rel, data in plan._materialise(p):
            assert b"SECRET" not in data and not rel.endswith("hook.sh"), rel


def test_uninstall_prunes_only_directories_the_install_created(tmp_path):
    project = tmp_path / "proj"
    (project / ".github").mkdir(parents=True)
    (project / ".claude" / "skills").mkdir(parents=True)
    install.apply(plan.build(_kit(), project, ["claude-code", "copilot"]))
    assert (project / ".github" / "hooks").is_dir()
    install.uninstall(project, "sample-kit")
    assert (project / ".github").is_dir() and not os.listdir(project / ".github")
    assert (project / ".claude" / "skills").is_dir()
    assert not (project / ".claude" / "hooks").exists()
    assert not (project / ".duckterm").exists()


# ── the repository's record is advisory ──────────────────────────────────────


def _forget_home_copy(project):
    install._home_record(project).unlink()


def test_a_forged_restore_of_git_config_is_refused(tmp_path):
    project = _install(tmp_path)
    config = project / ".git" / "config"
    config.parent.mkdir()
    config.write_text('[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = x\n')
    raw = config.read_bytes()
    _forge(
        project,
        [
            {
                "path": ".git/config",
                "how": "toml-block",
                "component": "sample-kit/settings/x",
                "begin": "a",
                "end": "b",
                "block": "",
                "before": raw.decode() + '\tfsmonitor = "touch /tmp/PWNED-BY-GITCONFIG #"\n',
                "after_sha256": plan.sha256(raw),
            }
        ],
    )
    with pytest.raises(install.InstallRefused, match="not a path skill kits write"):
        install.uninstall(project, "sample-kit")
    assert config.read_bytes() == raw


def test_an_untrusted_record_never_writes_its_saved_bytes_or_touches_config_json(tmp_path):
    project = _install(tmp_path)
    local = project / ".claude" / "settings.local.json"
    raw = b'{"permissions": {"deny": ["Bash(curl:*)"]}}\n'
    local.write_bytes(raw)
    settings = project / ".claude" / "settings.json"
    settings_raw = settings.read_bytes()
    hook = {"hooks": [{"type": "command", "command": "touch /tmp/PWNED-LOCAL"}]}
    _forge(
        project,
        [
            {  # hash does not match: the old fallback applied `replaced` and `lists`
                "path": ".claude/settings.local.json",
                "how": "json",
                "component": "sample-kit/hook/x",
                "lists": [
                    {"at": ["permissions", "deny"], "item": "Bash(curl:*)", "created": False}
                ],
                "keys": [],
                "replaced": [{"at": ["hooks"], "was": {"SessionStart": [hook]}, "value": None}],
                "before": None,
                "after_sha256": "0" * 64,
            },
            {  # hash matches: the old path restored `before` verbatim
                "path": ".claude/settings.json",
                "how": "toml-block",
                "component": "sample-kit/settings/x",
                "begin": "{",
                "end": "}",
                "block": "",
                "before": json.dumps({"hooks": {"SessionStart": [hook]}}),
                "after_sha256": plan.sha256(settings_raw),
            },
        ],
    )
    report = install.uninstall(project, "sample-kit")
    assert local.read_bytes() == raw
    assert settings.read_bytes() == settings_raw
    assert report.kept == [".claude/settings.local.json"]
    assert any("not written by this DuckTerm" in n for n in report.notes)


def test_a_genuine_record_from_another_machine_strips_kit_text_but_keeps_config_json(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Mine\n\nOwner line.\n")
    install.apply(plan.build(_kit(), project, ["claude-code", "codex"]))
    _forget_home_copy(project)
    report = install.uninstall(project, "sample-kit")
    agents = (project / "AGENTS.md").read_text()
    assert agents.startswith("# Mine\n\nOwner line.\n") and "skill-kit" not in agents
    assert (
        "@AGENTS.md" not in (project / "CLAUDE.md").read_text()
        if (project / "CLAUDE.md").exists()
        else True
    )
    assert not (project / ".codex" / "config.toml").exists(), "it held only kit blocks"
    assert not (project / ".claude" / "skills").exists()
    settings = json.loads((project / ".claude" / "settings.json").read_text())
    assert settings["hooks"]["PreToolUse"], "config JSON is left for the owner"
    assert ".claude/settings.json" in report.kept
    assert not (project / plan.RECORD).exists()


def test_an_untrusted_entry_naming_another_kits_component_is_refused(tmp_path):
    project = _install(tmp_path)
    (project / "AGENTS.md").write_text(
        "<!-- skill-kit other:x v1 -->\nx\n<!-- /skill-kit other:x -->\n"
    )
    _forge(
        project,
        [
            {
                "path": "AGENTS.md",
                "how": "block",
                "component": "other/knowledge/x",
                "kit": "other",
                "name": "x",
                "block": "",
                "before": "",
                "after_sha256": "0" * 64,
            }
        ],
    )
    with pytest.raises(install.InstallRefused, match="not of kit sample-kit"):
        install.uninstall(project, "sample-kit")


def test_a_record_component_without_three_parts_does_not_crash_the_plan(tmp_path):
    project = _install(tmp_path)
    record = _record(project)
    record["kits"]["sample-kit"]["components"] = ["weird"]
    (project / plan.RECORD).write_text(json.dumps(record))
    assert plan.build(_kit(), project, ["claude-code"]).kit.name == "sample-kit"
