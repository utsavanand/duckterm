"""A handoff reader must retain old constraints without another writer call."""

from copy import deepcopy

import pytest
from test_memory_folding import leaf

from duckterm import memory_assembly, memory_contract, memory_summary


def rows(count):
    captured = {"source": {"scope_hash": "scope"}, "conversation": {}, "policy": "test"}
    return [
        {**leaf(captured, n), "id": str(n), "fold": {"tier": 0, "children": []}}
        for n in range(count)
    ]


def test_new_span_does_not_erase_an_older_constraint():
    values = rows(4)
    before = deepcopy(values)
    result = memory_assembly.assemble(values)
    assert result["context"]["constraints"] == [
        row["continuity"]["context"]["constraints"][0] for row in values
    ]
    assert values == before


def test_compacted_parent_resolves_original_claims_and_replaces_narrative_heads():
    values = rows(11)
    context = {"overview": "The first ten spans.", **{f: [] for f in memory_summary.FIELDS}}
    context["constraints"] = [
        {"id": row["continuity"]["context"]["constraints"][0]["id"], "ref_revision": row["id"]}
        for row in values[:10]
    ]
    parent = {
        **values[0],
        "id": "parent",
        "fold": {"tier": 1, "children": [str(i) for i in range(10)]},
        "continuity": {"context": context},
    }
    result = memory_assembly.assemble([*values[:10], parent, values[10]])
    assert len(result["heads"]) == 2
    assert len(result["context"]["constraints"]) == 11
    assert result["narratives"][0]["text"] == "The first ten spans."


def test_missing_or_unverified_evidence_never_becomes_a_partial_union():
    values = rows(2)
    values[0]["summary_validation"]["ready"] = False
    with pytest.raises(ValueError, match="incomplete_span_input"):
        memory_assembly.assemble(values)
    values = rows(2)
    values[1]["continuity"]["context"]["constraints"] = [
        {
            "id": values[0]["continuity"]["context"]["constraints"][0]["id"],
            "ref_revision": "missing",
        }
    ]
    with pytest.raises(ValueError, match="unreadable_claim_reference"):
        memory_assembly.assemble(values)


def test_protected_overflow_reports_gap_instead_of_trimming():
    with pytest.raises(memory_contract.BudgetExceeded) as failure:
        memory_assembly.assemble(rows(100))
    assert failure.value.coverage["omitted"][0]["reason"] == "budget_exhausted"


def test_legacy_remains_identified_and_is_not_relabelled_as_a_span():
    values = rows(1)
    legacy = {**deepcopy(values[0]), "id": "legacy", "span": "cumulative"}
    result = memory_assembly.assemble([legacy, *values])
    assert result["legacy_revisions"] == ["legacy"]
    assert len(result["heads"]) == 1
    assert legacy["span"] == "cumulative"


def test_explicitly_retired_history_does_not_consume_open_handoff_budget(monkeypatch):
    from duckterm import memory_lifecycle

    values = rows(100)
    before = deepcopy(values)

    def resolved_lifecycle(context, links, sources):
        # Exercise aggregate sizing after authorized transitions have resolved;
        # the real transition's evidence/authority is covered by lifecycle tests.
        value = deepcopy(context)
        for claim in value["constraints"][:-1]:
            claim["state"] = "retired"
        return value

    monkeypatch.setattr(memory_lifecycle, "project", resolved_lifecycle)
    result = memory_assembly.assemble(values, links=[{}], active_only=True)
    assert len(result["context"]["constraints"]) == 1
    assert (
        result["context"]["constraints"][0] == values[-1]["continuity"]["context"]["constraints"][0]
    )
    assert values == before
