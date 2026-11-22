"""Consolidation changes approved memory only after a validated review."""

import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from duckterm.core.agents_rules import RuleBlock, load_rules, read_catalog, save_rules
from duckterm.core.rule_proposals import parse_decision, proposal, review_proposals
from duckterm.llm.suggest import Correction, suggest_rules
from duckterm.llm.summarizer import Summary


def rule() -> RuleBlock:
    return RuleBlock(
        id="gate", text="Do not pipe gate output.", title="Preserve exit status", status="active"
    )


def decision(action: str = "add_example") -> dict:
    return {
        "claim": "Piping through head also hides failures.",
        "memory_kind": "procedural",
        "source_refs": ["event:1"],
        "action": action,
        "reason": "The existing rule covers this behavior.",
        "target": {"rule_id": "gate", "base_revision": 1},
        "changes": {
            "example": {
                "situation": "Read part of the log",
                "do": "Read the saved log after the gate exits.",
                "avoid": "scripts/gate.sh | head -20",
            }
        },
        "context": None,
    }


def test_example_does_not_replace_instruction_or_activate_before_acceptance(tmp_path: Path) -> None:
    original = rule()
    p = proposal(parse_decision(decision(), [original], {"event:1"}))
    pending, history = review_proposals([original], [p], [])
    assert pending == [original]
    p["status"] = "accepted"
    accepted, history = review_proposals([original], [p], history)
    assert len(accepted) == 1 and accepted[0].id == "gate"
    assert accepted[0].text == original.text and accepted[0].scope == original.scope
    assert accepted[0].examples[0]["avoid"] == "scripts/gate.sh | head -20"
    assert accepted[0].revision == 2
    save_rules(tmp_path, accepted, proposals=history)
    assert load_rules(tmp_path) == accepted
    rendered = (tmp_path / "AGENTS.md").read_text()
    assert "Preserve exit status" in rendered and "Avoid: scripts/gate.sh | head -20" in rendered
    repeated, _ = review_proposals(accepted, history, history)
    assert repeated == accepted


def test_revision_changes_instruction_with_stale_revision_protection() -> None:
    d = decision("revise_rule")
    d["changes"] = {"fields": {"text": "Do not pipe any test output."}}
    p = {**proposal(parse_decision(d, [rule()])), "status": "accepted"}
    changed, _ = review_proposals([rule()], [p], [])
    assert changed[0].text == "Do not pipe any test output."
    assert changed[0].revision == 2
    with pytest.raises(ValueError, match="changed rule"):
        review_proposals(changed, [p], [])


def test_episode_cannot_install_rule() -> None:
    d = decision("retain_context")
    d.update(
        memory_kind="episodic",
        target=None,
        changes=None,
        context={"destination": "summary", "text": "Gate failed; fix is pending."},
    )
    p = proposal(parse_decision(d, [rule()], {"event:1"}))
    output, _ = review_proposals([rule()], [p], [])
    assert output == [rule()]
    p["status"] = "accepted"
    with pytest.raises(ValueError, match="not an applicable"):
        review_proposals([rule()], [p], [])
    d["action"] = "create_rule"
    with pytest.raises(ValueError, match="procedural"):
        parse_decision(d, [])


def test_evidence_is_deduplicated_without_new_prompt_text() -> None:
    d = decision("attach_evidence")
    d["changes"] = None
    d["source_refs"] = ["event:1", "event:1"]
    p = {**proposal(parse_decision(d, [rule()])), "status": "accepted"}
    output, _ = review_proposals([rule()], [p], [])
    assert output[0].source_refs == ["event:1"]
    assert output[0].text == rule().text and output[0].examples == []


@pytest.mark.parametrize(
    "change",
    [
        {"source_refs": ["invented"]},
        {"target": {"rule_id": "missing", "base_revision": 1}},
        {"changes": {"example": {"situation": "x"}}},
        {"action": "install"},
        {"approved": True},
    ],
)
def test_invalid_proposals_fail_closed(change: dict) -> None:
    with pytest.raises(ValueError):
        parse_decision({**decision(), **change}, [rule()], {"event:1"})


def test_rejected_target_and_review_history_cannot_be_rewritten() -> None:
    rejected = rule()
    rejected.status = "rejected"
    with pytest.raises(ValueError, match="active rule"):
        parse_decision(decision(), [rejected])
    p = {**proposal(parse_decision(decision(), [rule()])), "status": "rejected"}
    with pytest.raises(ValueError, match="cannot be rewritten"):
        review_proposals([rule()], [{**p, "status": "accepted"}], [p])
    with pytest.raises(ValueError, match="cannot be deleted"):
        review_proposals([rule()], [], [p])


def test_new_rule_gets_stable_identity_after_review() -> None:
    d = decision("create_rule")
    d.update(
        target=None,
        changes={
            "rule": {"title": "Stdlib", "text": "Keep the server dependency-free.", "scope": "all"}
        },
    )
    p = {**proposal(parse_decision(d, [rule()])), "status": "accepted"}
    output, history = review_proposals([rule()], [p], [])
    assert len(output) == 2 and output[1].status == "active"
    assert output[1].id != "keep-the-server-dependency-free"
    same, _ = review_proposals(output, history, history)
    assert same == output
    with pytest.raises(ValueError, match="already exists"):
        review_proposals(output, [p], [])


def test_catalog_rejects_stale_save_and_detects_external_edits(tmp_path: Path) -> None:
    save_rules(tmp_path, [rule()])
    first = read_catalog(tmp_path)
    updated = copy.deepcopy(rule())
    updated.explanation = "Pipelines may hide a failure."
    save_rules(tmp_path, [updated], expected_version=first["version"])
    with pytest.raises(ValueError, match="changed"):
        save_rules(tmp_path, [rule()], expected_version=first["version"])
    path = tmp_path / "AGENTS.md"
    path.write_text(path.read_text() + "\nOwner's extra instruction.\n")
    drift = read_catalog(tmp_path)
    assert drift["drift"]
    with pytest.raises(ValueError, match="differs"):
        save_rules(tmp_path, [updated], expected_version=drift["version"])
    save_rules(tmp_path, [updated], expected_version=drift["version"], reconcile_drift=True)
    assert not read_catalog(tmp_path)["drift"]
    assert "Owner's extra instruction" in next(tmp_path.glob("AGENTS.md.before-*")).read_text()


def test_suggestion_prompt_compares_full_rules_and_rejected_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []
    old = {**proposal(parse_decision(decision(), [rule()])), "status": "rejected"}

    def fake(prompt: str, **_kw) -> Summary:
        seen.append(prompt)
        return Summary(text=json.dumps({"decisions": [decision()]}), backend="test")

    monkeypatch.setattr("duckterm.llm.suggest.summarize", fake)
    result = suggest_rules(
        [Correction("user-prompt", "head hid the failure", ref="event:1")], [rule()], [old]
    )
    assert result[0]["action"] == "add_example"
    assert '"revision": 1' in seen[0] and '"status": "rejected"' in seen[0]
    assert "episodic" in seen[0] and "Do not default to adding a rule" in seen[0]


def test_unknown_version_or_structured_fields_are_not_silently_dropped(tmp_path: Path) -> None:
    raw = {"schema_version": 3, "rules": [asdict(rule())]}
    (tmp_path / ".duckterm-rules.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="schema version"):
        load_rules(tmp_path)
    raw["schema_version"] = 2
    raw["rules"][0]["future_field"] = "must not disappear"
    (tmp_path / ".duckterm-rules.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Unknown rule"):
        load_rules(tmp_path)


@pytest.mark.parametrize("raw", [[], {"rules": {}}, {"schema_version": 2, "rules": [{}]}])
def test_malformed_catalogue_fails_without_losing_rules(tmp_path: Path, raw: object) -> None:
    (tmp_path / ".duckterm-rules.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        read_catalog(tmp_path)


def test_external_json_edit_also_requires_reconciliation(tmp_path: Path) -> None:
    save_rules(tmp_path, [rule()])
    path = tmp_path / ".duckterm-rules.json"
    raw = json.loads(path.read_text())
    raw["rules"][0]["text"] = "An instruction edited outside the dashboard."
    path.write_text(json.dumps(raw))
    assert read_catalog(tmp_path)["drift"]


def test_owner_can_narrow_new_rule_to_a_harness() -> None:
    d = decision("create_rule")
    d.update(target=None, changes={"rule": {"title": "Gate", "text": "Run gate.", "scope": "all"}})
    p = {**proposal(parse_decision(d, [])), "status": "accepted", "scope_override": "codex"}
    output, history = review_proposals([], [p], [])
    assert output[0].scope == "codex"
    assert history[0]["changes"]["rule"]["scope"] == "all"
    p["scope_override"] = "global"
    with pytest.raises(ValueError, match="scope"):
        review_proposals([], [p], [])
