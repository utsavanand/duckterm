"""Canonical readers retain legacy shape and never infer retirement authority."""

from copy import deepcopy

import pytest

from duckterm import memory_contract as contract
from duckterm import memory_summary
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
    assert contract.claim_kind(read["goals"][0]) == "unclassified"
    assert contract.claim_state(read["goals"][0]) == "open"


def test_identity_is_independent_of_reference_order_but_not_claim_or_field():
    original = contract.claim_id("decisions", "Keep", ["source:v:b", "source:v:a"])
    assert original == contract.claim_id("decisions", "Keep", ["source:v:a", "source:v:b"])
    assert original != contract.claim_id("constraints", "Keep", ["source:v:a", "source:v:b"])
    assert original != contract.claim_id("decisions", "Change", ["source:v:a", "source:v:b"])


def test_v2_read_preserves_subtypes_and_refuses_oversized_view():
    value = version_two()
    value["constraints"][0]["kind"] = "callout"
    value["decisions"][0]["kind"] = "learning"
    assert contract.read_context(value, 2) == value
    # The complete oversized view is refused; no shortened memory is returned.
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
    original = deepcopy(value)
    with pytest.raises(contract.BudgetExceeded) as exc:
        contract.read_context(value, 2)
    assert value == original
    assert contract.read_coverage(exc.value.coverage)["omitted"][0]["reason"] == "budget_exhausted"


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


def revision(identity, value):
    return {"id": identity, "context_version": 2, "continuity": {"context": value}}


def referenced(value, target):
    return {
        "overview": value["overview"],
        **{name: [{"id": c["id"], "ref_revision": target} for c in value[name]] for name in FIELDS},
    }


def test_reference_assembly_reads_original_claims_without_storing_another_copy():
    value = version_two()
    rows = {
        "first": revision("first", value),
        "parent": revision("parent", referenced(value, "first")),
        "grandparent": revision("grandparent", referenced(value, "parent")),
    }
    original = deepcopy(rows)
    read, origins = contract.resolve_revision(rows["grandparent"], rows.get)
    assert read == value
    assert set(origins.values()) == {"first"}
    assert rows == original
    read["goals"][0]["text"] = "Changed view"
    assert rows == original


def test_missing_or_unauthorized_referenced_revision_never_returns_partial_context():
    value = version_two()
    with pytest.raises(ValueError, match="unreadable_claim_reference"):
        contract.resolve_revision(
            revision("parent", referenced(value, "outside-scope")), lambda _: None
        )


def test_reference_cannot_change_claim_kind_or_point_to_a_different_identity():
    value = version_two()
    parent = revision("parent", referenced(value, "first"))
    parent["continuity"]["context"]["goals"][0]["id"] = value["decisions"][0]["id"]
    with pytest.raises(ValueError, match="unreadable_claim_reference"):
        contract.resolve_revision(parent, {"first": revision("first", value)}.get)


def test_reference_cycles_fail_explicitly_without_dropping_claims():
    value = version_two()
    rows = {
        "first": revision("first", referenced(value, "second")),
        "second": revision("second", referenced(value, "first")),
    }
    with pytest.raises(ValueError, match="claim_reference_cycle"):
        contract.resolve_revision(rows["first"], rows.get)


def test_deep_revision_chain_fails_explicitly_before_recursion_overflow():
    value = version_two()
    rows = {"0": revision("0", value)}
    for n in range(1, 130):
        rows[str(n)] = revision(str(n), referenced(value, str(n - 1)))
    with pytest.raises(ValueError, match="claim_reference_depth"):
        contract.resolve_revision(rows["129"], rows.get)


def provider_context():
    value = version_two()
    for field in FIELDS:
        for item in value[field]:
            item["state"] = "open"
    return value


def test_existing_parser_accepts_typed_and_legacy_claims_only_under_explicit_v2():
    import json

    value = provider_context()
    value["goals"].append({"text": "Old goal", "refs": ["source:version:record"]})
    text = json.dumps(value)
    with pytest.raises(memory_summary.InvalidContext):
        memory_summary.parse(text, {"source:version:record"})
    result = memory_summary.parse(text, {"source:version:record"}, context_version=2)
    assert result == value
    assert contract.claim_kind(result["goals"][-1]) == "unclassified"
    assert "kind" not in result["goals"][-1]


@pytest.mark.parametrize("mutation", ["extra", "missing_state", "bad_id", "kind", "reference"])
def test_typed_parser_keeps_strict_keys_identity_and_citation_checks(mutation):
    import json

    value = provider_context()
    claim = value["constraints"][0]
    if mutation == "extra":
        claim["owner_approved"] = True
    elif mutation == "missing_state":
        claim.pop("state")
    elif mutation == "bad_id":
        claim["id"] = "f" * 16
    elif mutation == "kind":
        claim["kind"] = "risk"
    else:
        claim["refs"] = ["not:a:source"]
    with pytest.raises(memory_summary.InvalidContext):
        memory_summary.parse(json.dumps(value), {"source:version:record"}, context_version=2)


def test_provider_and_saved_reader_refuse_oversized_context_without_trimming():
    import json

    value = provider_context()
    for n in range(10):
        text = f"Constraint {n}:" + "x" * 1500
        refs = ["source:version:record"]
        value["constraints"].append(
            {
                "text": text,
                "refs": refs,
                "kind": "constraint",
                "state": "open",
                "id": contract.claim_id("constraints", text, refs),
            }
        )
    with pytest.raises(contract.BudgetExceeded):
        contract.read_context(value, 2)
    with pytest.raises(memory_summary.InvalidContext) as exc:
        memory_summary.parse(json.dumps(value), {"source:version:record"}, context_version=2)
    assert exc.value.reason == "context_too_large"


def typed_plan():
    return {
        "context_version": 2,
        "selected": [{"source": "source", "version": "version", "record": "record"}],
        "prior_context": None,
    }


def test_maintained_draft_assigns_claim_ids_in_code_without_requiring_model_hashes():
    import json

    from duckterm import memory_continuity

    value = provider_context()
    expected = deepcopy(value)
    for field in FIELDS:
        for item in value[field]:
            item.pop("id")
    value["goals"][0]["refs"] = "source:version:record"
    assert memory_continuity.context(json.dumps({"context": value}), typed_plan()) == expected


@pytest.mark.parametrize("transition", ["retired", "superseded", "replacement"])
def test_provider_cannot_retire_claims_merely_by_citing_an_available_source(transition):
    import json

    from duckterm import memory_continuity

    value = provider_context()
    if transition == "replacement":
        value["decisions"][0]["supersedes"] = "old-claim"
    else:
        value["decisions"][0]["state"] = transition
    with pytest.raises(memory_summary.InvalidContext) as exc:
        memory_continuity.context(json.dumps({"context": value}), typed_plan())
    assert exc.value.reason == "lifecycle_requires_authorization"


def test_typed_write_does_not_accept_prior_summary_context_as_new_span_evidence():
    import json

    from duckterm import memory_continuity

    plan = {**typed_plan(), "prior_context": context()}
    with pytest.raises(memory_summary.InvalidContext) as exc:
        memory_continuity.context(json.dumps({"context": provider_context()}), plan)
    assert exc.value.reason == "typed_write_requires_span"


def test_pointer_union_over_budget_declares_gap_without_changing_any_revision():
    rows = {}
    parent = {"overview": "All work", **{field: [] for field in FIELDS}}
    for n in range(12):
        text, refs = f"Constraint {n}: " + "🔒" * 300, [f"source:version:{n}"]
        claim = {
            "id": contract.claim_id("constraints", text, refs),
            "kind": "constraint",
            "state": "open",
            "text": text,
            "refs": refs,
        }
        value = {"overview": "One span", **{field: [] for field in FIELDS}}
        value["constraints"] = [claim]
        rows[str(n)] = revision(str(n), value)
        parent["constraints"].append({"id": claim["id"], "ref_revision": str(n)})
    rows["parent"] = revision("parent", parent)
    original = deepcopy(rows)
    with pytest.raises(contract.BudgetExceeded) as exc:
        contract.resolve_revision(rows["parent"], rows.get)
    gap = contract.read_coverage(exc.value.coverage)["omitted"][0]
    assert gap["reason"] == "budget_exhausted" and gap["count"] == 12
    assert len(gap["sample_refs"]) == 8
    assert rows == original
    # A refused aggregate does not make any original inaccessible.
    for n in range(12):
        assert (
            contract.resolve_revision(rows[str(n)], rows.get)[0]
            == rows[str(n)]["continuity"]["context"]
        )
