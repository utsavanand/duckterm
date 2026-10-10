"""Equivalent single citations normalize; unknown evidence remains rejected."""

import json

import pytest

from duckterm import memory_continuity, memory_summary
from duckterm.core.session_api import APIError


def fixture_plan():
    return {
        "selected": [{"source": "a" * 32, "version": "b" * 64, "record": "17"}],
        "prior_context": None,
    }


def response(reference):
    return json.dumps(
        {
            "context": {
                "overview": "Keep the owner constraint.",
                **{name: [] for name in memory_summary.FIELDS},
                "constraints": [{"text": "Keep the owner constraint.", "refs": reference}],
            }
        }
    )


def test_known_single_reference_normalizes_without_changing_claim_or_source():
    plan = fixture_plan()
    ref = "a" * 32 + ":" + "b" * 64 + ":17"
    value = memory_continuity.context(response(ref), plan)
    assert value["constraints"] == [{"text": "Keep the owner constraint.", "refs": [ref]}]
    assert value == memory_continuity.context(response([ref]), plan)


@pytest.mark.parametrize(
    "reference", ["unknown", "", [], ["unknown"], "first,second", {"ref": "unknown"}]
)
def test_malformed_or_unknown_citations_are_not_repaired(reference):
    with pytest.raises(APIError, match="invalid or oversized"):
        memory_continuity.context(response(reference), fixture_plan())


def test_normalized_reference_does_not_bypass_context_budget():
    ref = "a" * 32 + ":" + "b" * 64 + ":17"
    value = json.loads(response(ref))
    value["context"]["constraints"] *= 80
    with pytest.raises(APIError, match="invalid or oversized"):
        memory_continuity.context(json.dumps(value), fixture_plan())
