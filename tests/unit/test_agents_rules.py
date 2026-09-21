"""Typed AGENTS.md rules: round-trip, rendering, candidate merge with rejected
tombstones, and scope matching/inference."""

from pathlib import Path

from duckterm.core.agents_rules import (
    GENERATED_BANNER,
    RuleBlock,
    infer_scope,
    load_rules,
    merge_candidates,
    render_agents_md,
    rule_id,
    save_rules,
)


def _rule(**kw) -> RuleBlock:
    base = dict(id="no-piped-gates", text="Never pipe the gate.", status="active")
    base.update(kw)
    return RuleBlock(**base)


def test_save_load_round_trip(tmp_path: Path) -> None:
    rules = [
        _rule(),
        _rule(id="codex-approvals", scope="codex", status="candidate", source="digest"),
    ]
    save_rules(tmp_path, rules)
    loaded = load_rules(tmp_path)
    assert [r.id for r in loaded] == ["no-piped-gates", "codex-approvals"]
    assert loaded[1].scope == "codex"
    assert loaded[1].status == "candidate"


def test_save_renders_agents_md_with_banner_and_only_active(tmp_path: Path) -> None:
    save_rules(
        tmp_path,
        [
            _rule(heading="Shipping"),
            _rule(id="maybe", text="A pending idea.", status="candidate"),
            _rule(id="dead", text="A rejected idea.", status="rejected"),
        ],
    )
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.startswith(GENERATED_BANNER)
    assert "## Shipping" in text
    assert "Never pipe the gate." in text
    assert "pending idea" not in text
    assert "rejected idea" not in text


def test_render_labels_scoped_rules() -> None:
    text = render_agents_md(
        [
            _rule(),
            _rule(id="codex-rule", text="Answer approvals in-terminal.", scope="codex"),
        ]
    )
    assert "Answer approvals in-terminal. *(codex only)*" in text
    assert "Never pipe the gate. *(" not in text  # scope "all" gets no label
    assert "skip rules scoped to a runtime that isn't you" in text


def test_load_skips_malformed_entries(tmp_path: Path) -> None:
    (tmp_path / ".duckterm-rules.json").write_text(
        '{"rules": [{"id": "ok", "text": "Fine."}, {"text": "no id"}, "junk", '
        '{"id": "extra", "text": "Ignores unknown keys.", "future_field": 1}]}'
    )
    loaded = load_rules(tmp_path)
    assert [r.id for r in loaded] == ["ok", "extra"]


def test_merge_skips_rejected_tombstones() -> None:
    existing = [_rule(id="always-rebase", text="Always rebase.", status="rejected")]
    merged = merge_candidates(existing, [RuleBlock(id="always-rebase", text="Always rebase.")])
    assert len(merged) == 1
    assert merged[0].status == "rejected"  # not resurrected, not duplicated


def test_merge_appends_new_as_candidate_and_grows_evidence() -> None:
    existing = [_rule(id="old", status="candidate", evidence=2)]
    merged = merge_candidates(
        existing,
        [
            RuleBlock(id="old", text="Old rule.", evidence=4),
            RuleBlock(id="new", text="New rule.", status="active"),  # forced to candidate
        ],
    )
    by_id = {r.id: r for r in merged}
    assert by_id["old"].evidence == 4
    assert by_id["new"].status == "candidate"


def test_scope_matching() -> None:
    assert _rule(scope="all").matches("codex")
    assert _rule(scope="codex").matches("codex")
    assert not _rule(scope="codex").matches("claude-code")
    scoped = _rule(scope="claude-code/fable-5")
    assert scoped.matches("claude-code", "claude-fable-5")
    assert not scoped.matches("claude-code", "claude-sonnet-5")
    assert not scoped.matches("claude-code", None)  # model unknown -> no match


def test_infer_scope() -> None:
    assert infer_scope({"codex", "claude-code"}, set()) == "all"
    assert infer_scope({"codex"}, set()) == "codex"
    assert infer_scope({"claude-code"}, {"claude-fable-5"}) == "claude-code/fable-5"
    assert infer_scope({"claude-code"}, {"claude-fable-5", "claude-sonnet-5"}) == "claude-code"


def test_rule_id_is_deterministic_slug() -> None:
    assert rule_id("Never pipe the gate through grep!") == "never-pipe-the-gate-through-grep"
    assert rule_id("Never pipe the gate through grep!") == rule_id(
        "never PIPE the gate, through grep"
    )
