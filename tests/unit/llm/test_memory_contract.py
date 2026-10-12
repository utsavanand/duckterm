"""Canonical readers retain legacy shape and never infer retirement authority."""

from copy import deepcopy

import pytest

from duckterm import memory_contract as contract
from duckterm.memory_summary import FIELDS


def context():
    return {
        "overview": "Saved work.",
        **{
            name: [{"text": "Remember " + name, "refs": ["source:version:record"]}]
            for name in FIELDS
        },
    }


def version_two():
    value = context()
    for name in FIELDS:
        item = value[name][0]
        item.update(
            id=contract.claim_id(name, item["text"], item["refs"]),
            kind=sorted(contract.KINDS[name])[0],
        )
    return value


def test_legacy_read_does_not_rewrite_or_classify_original():
    value = context()
    original = deepcopy(value)
    read = contract.read_context(value, valid_refs={"source:version:record"})
    assert read == original and value == original
    assert read is not value and "kind" not in read["goals"][0]


def test_identity_is_independent_of_reference_order_but_not_claim_or_field():
    original = contract.claim_id("decisions", "Keep", ["source:v:b", "source:v:a"])
    assert original == contract.claim_id("decisions", "Keep", ["source:v:a", "source:v:b"])
    assert original != contract.claim_id("constraints", "Keep", ["source:v:a", "source:v:b"])
    assert original != contract.claim_id("decisions", "Change", ["source:v:a", "source:v:b"])


def test_v2_read_preserves_subtypes_state_and_large_protected_set():
    value = version_two()
    value["constraints"][0]["kind"] = "callout"
    value["decisions"][0]["kind"] = "learning"
    # Reading saved facts must not silently apply a single provider budget.
    for n in range(100):
        text = f"Standing constraint {n}: " + "x" * 150
        refs = ["source:version:record"]
        value["constraints"].append(
            {
                "text": text,
                "refs": refs,
                "id": contract.claim_id("constraints", text, refs),
                "kind": "constraint",
                "state": "open",
            }
        )
    assert contract.read_context(value, 2) == value


@pytest.mark.parametrize("version", [0, 3, True, "2", None])
def test_unsupported_versions_do_not_fall_back_to_v1(version):
    with pytest.raises(ValueError, match="unsupported_context_version"):
        contract.read_context(context(), version)


def test_unknown_evidence_and_field_kind_mismatch_are_rejected():
    with pytest.raises(ValueError, match="unknown_reference"):
        contract.read_context(version_two(), 2, valid_refs=set())
    value = version_two()
    value["risks"][0]["kind"] = "callout"
    with pytest.raises(ValueError, match="invalid_claim_kind"):
        contract.read_context(value, 2)


def test_reading_retirement_does_not_apply_it_or_drop_the_old_claim():
    value = version_two()
    value["goals"][0]["state"] = "retired"
    assert contract.read_context(value, 2) == value


def test_supersession_cycles_cannot_be_read_as_valid_context():
    value = version_two()
    first, second = value["decisions"][0], value["constraints"][0]
    first["supersedes"] = second["id"]
    second["supersedes"] = first["id"]
    with pytest.raises(ValueError, match="supersession_cycle"):
        contract.read_context(value, 2)


def test_absent_unknown_and_declared_coverage_remain_distinct():
    absent = contract.read_coverage(None)
    unknown = {"declared": True, "unknown": True, "omitted": []}
    complete = {"declared": True, "unknown": False, "omitted": []}
    assert absent != contract.read_coverage(unknown) != contract.read_coverage(complete)
    omitted = {
        "declared": True,
        "unknown": False,
        "omitted": [{"reason": "budget_exhausted", "count": 12, "sample_refs": ["s:v:r"]}],
    }
    assert contract.read_coverage(omitted) == omitted


@pytest.mark.parametrize(
    "omission",
    [
        {"reason": "unsupported", "count": 1, "sample_refs": []},
        {"reason": "pending_update", "count": True, "sample_refs": []},
        {"reason": "pending_update", "count": 1, "sample_refs": ["a", "b"]},
        {"reason": "pending_update", "count": 10, "sample_refs": ["a"] * 9},
    ],
)
def test_malformed_omissions_are_not_treated_as_complete(omission):
    with pytest.raises(ValueError, match="invalid_coverage"):
        contract.read_coverage({"declared": True, "unknown": False, "omitted": [omission]})
