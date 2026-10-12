"""The shared continuation budget preserves text or refuses without mutation."""

import json
from copy import deepcopy

import pytest

from duckterm import memory_budget, memory_handoff
from duckterm.core.session_api import APIError


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
def test_overflow_refuses_without_trimming_or_changing_coverage(part):
    metadata = {"coverage": {"declared": False, "unknown": True, "omitted": []}}
    before = deepcopy(metadata)
    values = {
        "narratives": [{"revision": "legacy", "text": "Narrative. " * 4000}],
        "required": json.dumps({"notes": "note " * 10000}),
        "source_map": [{"source": "s" * 100, "kind": "session"} for _ in range(500)],
    }
    with pytest.raises(APIError, match="No context was removed; source agent kept"):
        render(metadata=metadata, **{part: values[part]})
    assert metadata == before


def test_open_claim_overflow_names_kinds_and_never_slices_text():
    with pytest.raises(APIError, match="constraint"):
        render(protected={"constraints": [{"text": "Keep " * 10000, "kind": "constraint"}]})


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
    assert memory_handoff.TOOLS in text
