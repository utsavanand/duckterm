"""The continuation budget protects claims/tools and declares optional omissions."""

import json
from copy import deepcopy

import pytest

from duckterm import memory_budget, memory_contract, memory_handoff
from duckterm.core.session_api import APIError


@pytest.fixture(autouse=True)
def _brief_budget(monkeypatch):
    """Fixtures here are sized against a 32,000-byte budget; the rules don't depend on it."""
    monkeypatch.setattr(memory_budget, "MAX_BRIEF_BYTES", 32000)


def render(**kwargs):
    return memory_budget.render(
        introduction="Continue the named session",
        launch={},
        required=kwargs.pop("required", "{}"),
        protected=kwargs.pop("protected", {}),
        narratives=kwargs.pop("narratives", []),
        metadata=kwargs.pop("metadata", {}),
        source_map=kwargs.pop("source_map", []),
        tools=memory_handoff.TOOLS,
        **kwargs,
    )


@pytest.mark.parametrize("part", ["narratives", "required", "source_map"])
def test_optional_overflow_declares_gap_and_preserves_unknown_and_protected_claim(part):
    metadata = {"coverage": {"declared": False, "unknown": True, "omitted": []}}
    claim = {"text": "Do not delete owner files.", "kind": "constraint"}
    values = {
        "narratives": [{"revision": "legacy", "text": "Narrative. " * 4000}],
        "required": json.dumps(
            {"name": "product", "purpose": "Product design", "notes": "note " * 10000}
        ),
        "source_map": [
            {"source": str(i).zfill(32) + ":" + "a" * 64, "kind": "session"} for i in range(500)
        ],
    }
    original = deepcopy(values)
    text = render(metadata=metadata, protected={"constraints": [claim]}, **{part: values[part]})
    assert len(text.encode()) <= memory_budget.MAX_BRIEF_BYTES
    assert json.dumps(claim) in text
    assert memory_handoff.TOOLS in text
    assert json.dumps(metadata) in text
    coverage = memory_contract.read_coverage(metadata["coverage"])
    assert coverage["declared"] and coverage["unknown"]
    assert coverage["omitted"][0]["reason"] == "budget_exhausted"
    assert values == original
    if part == "required":
        assert '"name": "product"' in text and '"purpose": "Product design"' in text
        assert metadata["handoff_omissions"]["current_work_fields"] == ["notes"]


def test_open_claim_overflow_names_kinds_and_does_not_mutate_inputs():
    metadata = {"coverage": {"declared": False, "unknown": True, "omitted": []}}
    before = deepcopy(metadata)
    with pytest.raises(APIError, match="constraint"):
        render(
            metadata=metadata,
            protected={"constraints": [{"text": "Keep " * 10000, "kind": "constraint"}]},
        )
    assert metadata == before


def test_claims_narrative_tools_and_unknown_coverage_are_preserved():
    claim = {"text": "Do not remove the original files.", "kind": "constraint"}
    metadata = {"coverage": {"declared": True, "unknown": True, "omitted": []}}
    text = render(
        protected={"constraints": [claim]},
        narratives=[{"text": "Completed the parser fix."}],
        metadata=metadata,
    )
    assert json.dumps(claim) in text
    assert "Completed the parser fix." in text
    assert json.dumps(metadata) in text
    assert metadata["coverage"]["omitted"] == []
    assert memory_handoff.TOOLS in text


def test_recent_complete_narratives_win_over_old_narratives_and_source_catalog():
    prior_gap = {"reason": "unreadable_source", "count": 1, "sample_refs": ["old-source"]}
    metadata = {"coverage": {"declared": True, "unknown": False, "omitted": [prior_gap]}}
    narratives = [{"revision": str(i), "text": f"Narrative {i}: " + "🦆" * 1800} for i in range(8)]
    text = render(
        metadata=metadata,
        narratives=narratives,
        source_map=[{"source": "a" * 96} for _ in range(400)],
    )
    assert len(text.encode()) <= memory_budget.MAX_BRIEF_BYTES
    assert json.dumps(narratives[-1], ensure_ascii=False) in text
    assert "Narrative 0:" not in text
    assert metadata["coverage"]["unknown"] is False
    assert metadata["coverage"]["omitted"][0] == prior_gap
    assert metadata["handoff_omissions"]["source_references"] == 400
    assert 0 < metadata["handoff_omissions"]["summary_narratives"] < 8
    memory_contract.read_coverage(metadata["coverage"])
    assert memory_handoff.TOOLS in text


def test_more_than_ten_kb_of_protected_claims_can_fit_the_final_handoff_budget():
    claim = {"kind": "decision", "text": "Keep decision evidence. " * 600}
    text = render(protected={"decisions": [claim]})
    assert 10000 < len(json.dumps(claim).encode()) < memory_budget.MAX_BRIEF_BYTES
    assert json.dumps(claim) in text
    assert memory_handoff.TOOLS in text
