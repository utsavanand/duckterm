"""Reconciliation keeps each live item once and closes items only with evidence."""

import asyncio
import json

import pytest

from duckterm import memory_contract, memory_reconcile
from duckterm.memory_summary import FIELDS
from duckterm.persistence.digests import DigestStore


def leaf(identity, *items, summary="Work happened."):
    context = {"overview": summary, **{field: [] for field in FIELDS}}
    for kind, text, ref in items:
        field = memory_contract.KIND_FIELD[kind]
        context[field].append(
            {
                "id": memory_contract.claim_id(field, text, [ref]),
                "kind": kind,
                "text": text,
                "refs": [ref],
                "state": "open",
            }
        )
    return {"id": identity, "summary": summary, "continuity": {"context": context}}


def provider(ops):
    prompts = []

    async def generate(prompt):
        prompts.append(prompt)
        return json.dumps({"ops": ops})

    return prompts, generate


@pytest.fixture
def store(tmp_path):
    return DigestStore(tmp_path / "db.sqlite")


def run(store, leaves, replies):
    prompts, generate = provider(replies)
    result = asyncio.run(memory_reconcile.reconcile(store, "s", "scope", leaves, generate))
    return result, prompts, memory_reconcile.latest(store, "s", "scope")


def texts(saved):
    return [item["text"] for item in saved["items"]]


def test_duplicate_merge_done_and_silence(store):
    first = leaf(
        "a",
        ("constraint", "Ship through release-dev", "r:v:1"),
        ("task", "Fix dialog cap", "r:v:2"),
        ("authority", "Only the owner publishes", "r:v:3"),
    )
    second = leaf(
        "b",
        ("constraint", "Releases go via release-dev", "r:v:4"),
        ("constraint", "Release-dev alone tags; builders stop at push", "r:v:5"),
        summary="Dialog cap fixed and verified.",
    )
    # One numbering over all items: a gives n1-n3, b gives n4-n5.
    replies = [
        {"op": "duplicate", "id": "n4", "of": "n1"},
        {
            "op": "merge",
            "ids": ["n1", "n5"],
            "kind": "authority",
            "text": "Ship through release-dev; it alone tags, builders stop at push",
        },
        {"op": "done", "id": "n3", "by": "s2"},
    ]
    result, prompts, saved = run(store, [first, second], replies)
    assert result["state"] == "reconciled" and result["rejected_ops"] == 0
    assert texts(saved) == [
        "Ship through release-dev; it alone tags, builders stop at push",
        "Only the owner publishes",
    ]
    merged = saved["items"][0]
    assert merged["refs"] == ["r:v:1", "r:v:5"]
    assert len(merged["merged_from"]) == 2
    closed = {item["text"]: (item["op"], item["by"]) for item in saved["closed"]}
    assert closed["Fix dialog cap"] == ("done", "b")
    assert closed["Releases go via release-dev"][0] == "duplicate"
    # One provider call covers every category.
    assert len(prompts) == 1
    again, prompts, _ = run(store, [first, second], replies)
    assert again["state"] == "current" and prompts == []


@pytest.mark.parametrize(
    "op",
    [
        {"op": "drop", "id": "o1"},
        {"op": "done", "id": "o1"},
        {"op": "done", "id": "o1", "by": "s9"},
        {"op": "superseded", "id": "n1", "by": "o1"},
        {"op": "merge", "ids": ["o1"], "kind": "task", "text": "One"},
        {"op": "merge", "ids": ["o1", "n1"], "kind": "callout", "text": "Retired kind"},
        {"op": "rewrite", "id": "o1", "text": "Anything"},
        # Malformed shapes inside a parsed reply (main-qa, #364).
        42,
        {"op": ["done"], "id": "o1", "by": "s1"},
        {"op": "merge", "ids": [["o1"], "n1"], "kind": "task", "text": "Merged"},
        {"op": "done", "id": [], "by": "s1"},
        {"op": "done", "id": "o1", "by": []},
        {"op": "duplicate", "id": "n1", "of": {"id": "o1"}},
    ],
)
def test_unsupported_closures_keep_the_item(store, op):
    run(store, [leaf("a", ("task", "Old task", "r:v:1"))], [])
    result, _, saved = run(store, [leaf("b", ("task", "New task", "r:v:2"))], [op])
    assert result["rejected_ops"] == 1
    assert texts(saved) == ["Old task", "New task"]


def test_brief_adds_unreconciled_items_and_omits_lifecycle_closures(store):
    run(store, [leaf("a", ("decision", "Use spans", "r:v:1"))], [])
    saved = memory_reconcile.latest(store, "s", "scope")
    later = leaf("b", ("risk", "Unregistered domain in share URLs", "r:v:2"))
    brief = memory_reconcile.brief_items(saved, [later], set())
    assert brief["intent"] == []
    assert [(i["kind"], i["text"]) for i in brief["rules"]] == [("decision", "Use spans")]
    assert [(i["kind"], i["text"]) for i in brief["facts"]] == [
        ("risk", "Unregistered domain in share URLs")
    ]
    closed = {saved["items"][0]["id"]}
    assert memory_reconcile.brief_items(saved, [later], closed)["rules"] == []


def test_invalid_reply_twice_fails_without_saving(store):
    async def generate(prompt):
        return "not json"

    with pytest.raises(ValueError, match="invalid_reconcile_reply"):
        asyncio.run(
            memory_reconcile.reconcile(
                store, "s", "scope", [leaf("a", ("goal", "Ship", "r:v:1"))], generate
            )
        )
    saved = store._conn.execute(
        "SELECT COUNT(*) FROM digest_items WHERE bucket=?", (memory_reconcile.BUCKET,)
    ).fetchone()[0]
    assert saved == 0


@pytest.mark.parametrize("later_fact", [True, False])
def test_a_later_fact_supersedes_a_contradicting_rule_across_categories(store, later_fact):
    rule = leaf("a", ("constraint", "Notary keychain is a dedicated file; unlock it", "r:v:1"))
    fact = leaf("b", ("environment", "Notary credentials moved to the login keychain", "r:v:2"))
    leaves = [rule, fact] if later_fact else [fact, rule]
    # The rule is superseded by the fact; refs follow the leaves' order.
    op = {"op": "superseded", "id": "n1", "by": "n2"}
    if not later_fact:
        op = {"op": "superseded", "id": "n2", "by": "n1"}
    result, prompts, saved = run(store, leaves, [op])
    assert "Notary credentials moved" in prompts[0] and "Notary keychain" in prompts[0]
    remaining = texts(saved)
    if later_fact:
        assert remaining == ["Notary credentials moved to the login keychain"]
        assert saved["closed"][0]["op"] == "superseded"
    else:
        assert result["rejected_ops"] == 1
        assert len(remaining) == 2


def seeded(store, count, start=0, name="l1"):
    kinds = ["task", "decision", "known_issue"]
    items = [(kinds[n % 3], f"Item {n} detail", f"src:v:r{n}") for n in range(start, count)]
    asyncio.run(
        memory_reconcile.reconcile(store, "s", "scope", [leaf(name, *items)], provider([])[1])
    )
    return memory_reconcile.latest(store, "s", "scope")


def clean(store, replies):
    prompts = []

    async def generate(prompt):
        prompts.append(prompt)
        category = json.loads(prompt.split("INPUT:\n", 1)[1])["category"]
        reply = replies[category]
        return reply if isinstance(reply, str) else json.dumps({"ops": reply})

    summaries = [{"id": "rev-1", "summary": "Item 2 was fixed."}]
    result = asyncio.run(memory_reconcile.clean_up(store, "s", "scope", summaries, generate))
    return result, prompts, memory_reconcile.latest(store, "s", "scope")


def test_clean_up_waits_for_the_threshold(store):
    seeded(store, memory_reconcile.CLEANUP_AT - 1)
    result, prompts, _ = clean(store, {})
    assert result["state"] == "skipped" and prompts == []


def test_clean_up_merges_and_closes_with_evidence_and_leaves_failures_alone(store):
    saved = seeded(store, 160)
    ref = {i["text"]: f"o{n}" for n, i in enumerate(saved["items"], 1)}
    result, prompts, after = clean(
        store,
        {
            "facts": [
                {"op": "done", "id": ref["Item 2 detail"], "by": "s1"},
                {
                    "op": "merge",
                    "ids": [ref["Item 5 detail"], ref["Item 8 detail"]],
                    "kind": "known_issue",
                    "text": "Items 5 and 8 are one issue.",
                },
            ],
            # An id from another category is not this call's to close.
            "rules": [{"op": "done", "id": ref["Item 2 detail"], "by": "s1"}],
            "intent": "not json",
        },
    )
    assert len(prompts) == 4  # intent failed twice
    assert result["state"] == "cleaned" and result["failed_categories"] == ["intent"]
    assert (result["before"], result["after"], result["closed"]) == (160, 158, 3)
    remaining = {i["text"] for i in after["items"]}
    assert "Item 2 detail" not in remaining and "Items 5 and 8 are one issue." in remaining
    assert {"Item 0 detail", "Item 1 detail"} <= remaining
    assert after["absorbed"] == saved["absorbed"] and after["base"] == saved["id"]
    done = next(c for c in after["closed"] if c["text"] == "Item 2 detail")
    assert done["op"] == "done" and done["by"] == "rev-1"


def test_clean_up_reruns_only_after_the_list_grows(store):
    seeded(store, 160)
    assert clean(store, {"intent": [], "rules": [], "facts": []})[0]["state"] == "cleaned"
    assert clean(store, {})[0]["state"] == "skipped"
    seeded(store, 160 + memory_reconcile.CLEANUP_GROWTH, start=160, name="l2")
    assert clean(store, {"intent": [], "rules": [], "facts": []})[0]["state"] == "cleaned"
