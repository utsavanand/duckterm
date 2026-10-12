"""Compaction folds narrative, never delegates protected-claim loss to a model."""

import asyncio
import json
from copy import deepcopy

import pytest

from duckterm import memory_compaction as compaction
from duckterm import memory_contract, memory_summary


def node(identity, tier=0, **changes):
    return {
        "id": str(identity),
        "tier": tier,
        "context_version": 2,
        "coverage": {"declared": True, "unknown": False, "omitted": []},
        "context": {
            "overview": f"Work in span {identity}.",
            **{
                name: [
                    {
                        "id": memory_contract.claim_id(
                            name, f"Exact {name} {identity}.\n🔒", [f"source:version:{identity}"]
                        ),
                        "kind": sorted(memory_contract.KINDS[name])[0],
                        "text": f"Exact {name} {identity}.\n🔒",
                        "refs": [f"source:version:{identity}"],
                    }
                ]
                for name in memory_summary.FIELDS
            },
        },
        **changes,
    }


def providers():
    calls = []

    async def generate(prompt):
        calls.append(prompt)
        return json.dumps({"overview": "Work proceeded across the selected spans."})

    async def validate(prompt):
        calls.append(prompt)
        return json.dumps({"ready": True, "reason_codes": []})

    return calls, generate, validate


def test_threshold_uses_oldest_ten_of_the_incoming_tier_only():
    nodes = [node(n) for n in range(49)] + [node("parent", 1)]
    assert compaction.select(nodes, 0) == []
    nodes.append(node(49))
    assert [n["id"] for n in compaction.select(nodes, 0)] == [str(n) for n in range(10)]
    assert compaction.select(nodes, 1) == []


def test_first_hundred_writes_fold_five_times_without_losing_active_leaves():
    active = []
    folds = []

    def insert(item):
        children = compaction.select(active, item["tier"])
        if children:
            folds.append(item["id"])
            for child in children:
                active.remove(child)
            insert(node("parent-" + str(len(folds)), item["tier"] + 1))
        active.append(item)

    for n in range(1, 101):
        insert(node(n))
    assert folds == ["51", "61", "71", "81", "91"]
    assert sum(n["tier"] == 0 for n in active) == 50
    assert sum(n["tier"] == 1 for n in active) == 5


def test_protected_claims_survive_exactly_and_raw_history_never_enters_provider():
    children = [node(n, raw_turns="RAW HISTORY MUST NOT ENTER") for n in range(10)]
    original = deepcopy(children)
    calls, generate, validate = providers()
    result = asyncio.run(compaction.compact(children, generate, validate))
    assert result["state"] == "compacted" and result["tier"] == 1
    assert result["children"] == [str(n) for n in range(10)]
    for name in memory_summary.FIELDS:
        assert result["context"][name] == [c["context"][name][0] for c in children]
    assert children == original
    assert len(calls) == 2 and all("RAW HISTORY MUST NOT ENTER" not in c for c in calls)
    assert result["coverage"] == {"declared": True, "unknown": False, "omitted": []}


@pytest.mark.parametrize(
    "coverage",
    [
        None,
        {"declared": False, "unknown": True, "omitted": []},
        {"declared": True, "unknown": True, "omitted": []},
        {
            "declared": True,
            "unknown": False,
            "omitted": [{"reason": "pending_update", "count": 1, "sample_refs": []}],
        },
    ],
)
def test_incomplete_or_unknown_input_does_not_call_provider(coverage):
    children = [node(n) for n in range(10)]
    children[0]["coverage"] = coverage
    calls, generate, validate = providers()
    result = asyncio.run(compaction.compact(children, generate, validate))
    assert result == {"state": "pending", "reason": "incomplete_input", "children_preserved": True}
    assert calls == []


def test_unknown_fields_and_conflicting_claim_identities_are_not_discarded():
    children = [node(n) for n in range(10)]
    children[0]["context"]["future_kind"] = []
    with pytest.raises(ValueError, match="unsupported_context"):
        compaction.prepare(children)
    children[0]["context"].pop("future_kind")
    children[1]["context"]["goals"][0]["id"] = children[0]["context"]["goals"][0]["id"]
    with pytest.raises(ValueError, match="invalid_claim_identity"):
        compaction.prepare(children)


def test_learning_and_callout_subtypes_use_existing_canonical_fields():
    children = [node(n) for n in range(10)]
    for child in children:
        child["context"]["constraints"][0]["kind"] = "callout"
        child["context"]["decisions"][0]["kind"] = "learning"
    _, generate, validate = providers()
    result = asyncio.run(compaction.compact(children, generate, validate))
    assert result["state"] == "compacted"
    assert all(c["kind"] == "callout" for c in result["context"]["constraints"])
    assert all(c["kind"] == "learning" for c in result["context"]["decisions"])


def test_legacy_context_cannot_be_relabelled_as_a_complete_span():
    children = [node(n) for n in range(10)]
    del children[0]["context_version"]
    calls, generate, validate = providers()
    result = asyncio.run(compaction.compact(children, generate, validate))
    assert result["reason"] == "unclassified_context" and calls == []


def test_compaction_cannot_choose_between_conflicting_claim_lifecycle_states():
    children = [node(n) for n in range(10)]
    claim = deepcopy(children[0]["context"]["goals"][0])
    claim["state"] = "retired"
    children[1]["context"]["goals"].append(claim)
    calls, generate, validate = providers()
    result = asyncio.run(compaction.compact(children, generate, validate))
    assert result["reason"] == "conflicting_claim_identity" and calls == []


@pytest.mark.parametrize(
    "failure", ["generation", "validation", "timeout", "claim_edit", "malformed"]
)
def test_provider_failure_preserves_inputs_and_never_returns_a_parent(failure):
    children = [node(n) for n in range(10)]
    original = deepcopy(children)
    _, good_generate, good_validate = providers()

    async def generate(prompt):
        if failure == "generation":
            raise RuntimeError("private diagnostic")
        if failure == "timeout":
            await asyncio.Future()
        if failure == "claim_edit":
            return json.dumps({"overview": "Work", "goals": []})
        if failure == "malformed":
            return "not json"
        return await good_generate(prompt)

    async def validate(prompt):
        if failure == "validation":
            return json.dumps({"ready": False, "reason_codes": ["missing_context"]})
        return await good_validate(prompt)

    result = asyncio.run(compaction.compact(children, generate, validate, timeout=0.01))
    assert result["state"] == "pending" and result["children_preserved"]
    assert "context" not in result and children == original
    assert "private diagnostic" not in str(result)


def test_over_budget_input_remains_intact_and_costs_no_provider_call():
    children = [node(n) for n in range(10)]
    for child in children:
        claim = child["context"]["goals"][0]
        claim["text"] = "🔒" * 2000
        claim["id"] = memory_contract.claim_id("goals", claim["text"], claim["refs"])
    calls, generate, validate = providers()
    result = asyncio.run(compaction.compact(children, generate, validate))
    assert result["reason"] == "input_too_large" and result["children_preserved"]
    assert calls == []


@pytest.mark.parametrize("bad_tier", [False, 0.0, None, "0", -1])
def test_malformed_child_tiers_cannot_fold_as_tier_zero(bad_tier):
    children = [node(n) for n in range(10)]
    children[-1]["tier"] = bad_tier
    calls, generate, validate = providers()
    result = asyncio.run(compaction.compact(children, generate, validate))
    assert result["state"] == "pending" and result["reason"] == "mixed_tiers"
    assert calls == []


def test_cancelled_validation_propagates_without_producing_parent():
    children = [node(n) for n in range(10)]
    original = deepcopy(children)

    async def scenario():
        entered = asyncio.Event()
        _, generate, _ = providers()

        async def validate(prompt):
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(compaction.compact(children, generate, validate))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert children == original
