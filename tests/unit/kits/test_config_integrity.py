"""Config files the installer edits (not copies) come back byte-identical on
uninstall, and verify notices when they are deleted or their kit values are
gone. The first two tests are QA's regressions on #420 at 48323fdc."""

import json

import pytest
from test_skill_kits import _kit

from duckterm.kits import install, plan

ORIGINALS = [
    (
        "claude-code",
        ".claude/settings.json",
        b'{ "permissions": {"allow": ["Bash(ls *)"]}, "ownerEmpty": {} }\n',
    ),
    ("codex", ".codex/config.toml", b'# owner\nmodel = "fixture"\n\n\n'),
    ("opencode", "opencode.json", b'{ "model": "fixture" }\n'),
    ("copilot", "AGENTS.md", b"Owner instructions without final newline"),
]


@pytest.mark.parametrize("harness,path,raw", ORIGINALS)
def test_uninstall_restores_original_config_bytes(tmp_path, harness, path, raw):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    install.apply(plan.build(_kit(), tmp_path, [harness]))
    assert target.read_bytes() != raw
    install.uninstall(tmp_path, "sample-kit")
    assert target.read_bytes() == raw


@pytest.mark.parametrize(
    "harness,path",
    [
        ("claude-code", ".claude/settings.json"),
        ("codex", ".codex/config.toml"),
        ("opencode", "opencode.json"),
    ],
)
def test_verify_detects_deleted_generated_config(tmp_path, harness, path):
    install.apply(plan.build(_kit(), tmp_path, [harness]))
    target = tmp_path / path
    assert target.is_file()
    target.unlink()
    reports = install.verify(tmp_path)
    assert any(not r.ok() for r in reports)
    assert path in reports[0].missing


def test_verify_detects_a_kit_value_removed_from_a_shared_config(tmp_path):
    install.apply(plan.build(_kit(), tmp_path, ["claude-code"]))
    settings = tmp_path / ".claude" / "settings.json"
    config = json.loads(settings.read_text())
    del config["hooks"]["PreToolUse"]
    settings.write_text(json.dumps(config))
    (report,) = install.verify(tmp_path)
    assert ".claude/settings.json" in report.drifted and not report.missing

    # The owner adding their own key to the same file is not drift.
    config["hooks"]["PreToolUse"] = json.loads((tmp_path / plan.RECORD).read_text())["kits"][
        "sample-kit"
    ]["entries"]
    install.apply(plan.build(_kit(), tmp_path, ["claude-code"]))
    config = json.loads(settings.read_text())
    config["ownerKey"] = {"anything": 1}
    settings.write_text(json.dumps(config))
    (report,) = install.verify(tmp_path)
    assert report.ok()


def test_uninstall_after_owner_edits_removes_only_kit_additions(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"permissions": {"allow": ["Bash(ls *)"]}, "ownerEmpty": {}}\n')
    install.apply(plan.build(_kit(), tmp_path, ["claude-code"]))
    config = json.loads(settings.read_text())
    config["permissions"]["allow"].append("Bash(owner *)")
    config["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "echo owner"}]}]
    settings.write_text(json.dumps(config, indent=2) + "\n")
    install.uninstall(tmp_path, "sample-kit")
    config = json.loads(settings.read_text())
    assert config["ownerEmpty"] == {}
    assert config["permissions"]["allow"] == ["Bash(ls *)", "Bash(owner *)"]
    assert "deny" not in config["permissions"]
    assert config["hooks"] == {"Stop": [{"hooks": [{"type": "command", "command": "echo owner"}]}]}


def test_reinstall_keeps_the_original_baseline(tmp_path):
    raw = b'{ "model": "fixture" }\n'
    (tmp_path / "opencode.json").write_bytes(raw)
    install.apply(plan.build(_kit(), tmp_path, ["opencode"]))
    install.apply(plan.build(_kit(), tmp_path, ["opencode"]))
    install.uninstall(tmp_path, "sample-kit")
    assert (tmp_path / "opencode.json").read_bytes() == raw
    assert not (tmp_path / ".duckterm").exists()


def test_a_config_the_kit_created_is_removed_entirely(tmp_path):
    install.apply(plan.build(_kit(), tmp_path, ["codex", "copilot"]))
    assert (tmp_path / ".codex" / "config.toml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    install.uninstall(tmp_path, "sample-kit")
    assert not (tmp_path / ".codex").exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


# ── typed rules (QA, second review) ──────────────────────────────────────────


def _with_owner_rule(tmp_path):
    from duckterm.core import agents_rules

    agents_rules.save_rules(
        tmp_path, [agents_rules.RuleBlock(id="mine", text="Owner rule", status="active")]
    )
    install.apply(plan.build(_kit(), tmp_path, ["codex"]))
    return agents_rules


@pytest.mark.parametrize("mutation", ["text", "status"])
def test_verify_reports_changed_typed_kit_rule(tmp_path, mutation):
    agents_rules = _with_owner_rule(tmp_path)
    rules = agents_rules.load_rules(tmp_path)
    rule = next(r for r in rules if r.kit == "sample-kit")
    if mutation == "text":
        rule.text = "Different instruction"
    else:
        rule.status = "rejected"
    agents_rules.save_rules(tmp_path, rules)
    (report,) = install.verify(tmp_path)
    assert report.drifted == [f"rule {rule.id}"] and not report.missing


def test_owner_rules_added_beside_a_kit_rule_are_not_drift_and_survive_uninstall(tmp_path):
    agents_rules = _with_owner_rule(tmp_path)
    rules = agents_rules.load_rules(tmp_path)
    rules.append(agents_rules.RuleBlock(id="later", text="Added later", status="active"))
    agents_rules.save_rules(tmp_path, rules)
    assert all(r.ok() for r in install.verify(tmp_path))
    install.uninstall(tmp_path, "sample-kit")
    assert [r.id for r in agents_rules.load_rules(tmp_path)] == ["mine", "later"]
