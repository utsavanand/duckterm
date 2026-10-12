"""Compaction folds narrative, never delegates protected-claim loss to a model."""

import asyncio
import json
from copy import deepcopy

import pytest

from duckterm import memory_compaction as compaction
from duckterm import memory_summary


def node(identity, tier=0, **changes):
    return {
        "id": str(identity),
        "tier": tier,
        "complete": True,
        "gaps": [],
        "pending_updates": [],
        "unclassified": [],
        "context": {
            "overview": f"Work in span {identity}.",
            **{
                name: [
                    {
                        "id": name + str(identity),
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
    assert result["gaps"] == result["pending_updates"] == result["unclassified"] == []


@pytest.mark.parametrize("field", ["gaps", "pending_updates", "unclassified"])
def test_incomplete_or_unknown_input_does_not_call_provider(field):
    for missing in (False, True):
        children = [node(n) for n in range(10)]
        if missing:
            del children[0][field]
        else:
            children[0][field] = ["unresolved"]
        calls, generate, validate = providers()
        result = asyncio.run(compaction.compact(children, generate, validate))
        assert result == {
            "state": "pending",
            "reason": "incomplete_input",
            "children_preserved": True,
        }
        assert calls == []


def test_unknown_fields_and_conflicting_claim_identities_are_not_discarded():
    children = [node(n) for n in range(10)]
    children[0]["context"]["future_kind"] = []
    with pytest.raises(ValueError, match="unsupported_context"):
        compaction.prepare(children, memory_summary.FIELDS)
    children[0]["context"].pop("future_kind")
    children[1]["context"]["goals"][0]["id"] = children[0]["context"]["goals"][0]["id"]
    with pytest.raises(ValueError, match="conflicting_claim_identity"):
        compaction.prepare(children, memory_summary.FIELDS)


def test_explicitly_supported_extra_kinds_are_preserved_without_another_store():
    fields = (*memory_summary.FIELDS, "learnings", "callouts")
    children = [node(n) for n in range(10)]
    for child in children:
        for name in ("learnings", "callouts"):
            child["context"][name] = [{"text": "Preserve " + name, "refs": ["same:source:record"]}]
    _, generate, validate = providers()
    result = asyncio.run(compaction.compact(children, generate, validate, fields=fields))
    assert result["state"] == "compacted"
    assert result["context"]["learnings"] == children[0]["context"]["learnings"]
    assert result["context"]["callouts"] == children[0]["context"]["callouts"]


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
        child["context"]["goals"][0]["text"] = "🔒" * 2000
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
