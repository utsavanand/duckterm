"""Folded retrieval must retain typed claims without inventing source authority."""

import asyncio
import json
from copy import deepcopy

import pytest
from test_memory import memory_rig, transcript

from duckterm import memory_contract, memory_records, memory_summary

__all__ = ["memory_rig"]

REF = "original:version:0"


def revision(identity, claims, version=2):
    context = {"overview": "History", **{f: [] for f in memory_summary.FIELDS}}
    context["decisions"] = claims
    return {
        "id": identity,
        "created_at": 1,
        "context_version": version,
        "summary": "Sensitive complete narrative",
        "continuity": {"context": context, "verified": True, "processed": {"original": {}}},
    }


def claim(text="Use glacier", state="open"):
    return {
        "id": memory_contract.claim_id("decisions", text, [REF]),
        "text": text,
        "refs": [REF],
        "kind": "decision",
        "state": state,
    }


def project(rows, available=True):
    return memory_records.project(
        {"session": "agent", "root": "root", "canonical": {"revisions": rows, "checkpoints": []}},
        [{"id": "original", "version": "version", "records": [{"id": "0"}]}] if available else [],
    )


def test_folded_claim_keeps_origin_type_state_and_exact_citation():
    item = claim(state="retired")
    leaf = revision("leaf", [item])
    parent = revision("parent", [{"id": item["id"], "ref_revision": "leaf"}])
    top = revision("top", [{"id": item["id"], "ref_revision": "parent"}])
    before = deepcopy([leaf, parent, top])
    record = project(before)[-1]["records"][1]
    assert record["id"] == "decisions:0"
    assert (record["kind"], record["state"], record["preview"]) == (
        "decision",
        "retired",
        "Use glacier",
    )
    payload = json.loads(record["text"])
    assert payload["origin_revision"] == "leaf"
    assert payload["refs"] == [REF]
    assert before == [leaf, parent, top]


@pytest.mark.parametrize("failure", ["missing", "cycle"])
def test_failed_resolution_withholds_entire_claim_view_and_summary(failure):
    item = claim()
    parent = revision("parent", [{"id": item["id"], "ref_revision": "leaf"}])
    rows = [parent]
    if failure == "cycle":
        rows.insert(0, revision("leaf", [{"id": item["id"], "ref_revision": "parent"}]))
    records = project(rows)[-1]["records"]
    assert [r["role"] for r in records] == ["summary_metadata"]
    metadata = json.loads(records[0]["text"])
    assert metadata["retrieval_error"]
    assert metadata["retrieval_coverage"]["omitted"][0]["reason"] == "unreadable_source"


def test_revoked_original_does_not_expose_folded_claim_or_preview():
    item = claim()
    rows = [
        revision("leaf", [item]),
        revision("parent", [{"id": item["id"], "ref_revision": "leaf"}]),
    ]
    assert all(
        [r["role"] for r in s["records"]] == ["summary_metadata"]
        for s in project(rows, available=False)
    )


def test_legacy_claim_is_displayed_unclassified_without_mutation():
    row = revision("old", [{"text": "Legacy glacier", "refs": [REF]}], version=1)
    original = deepcopy(row)
    record = project([row])[0]["records"][1]
    assert record["kind"] == "unclassified"
    assert record["state"] == "open"
    assert row == original


def test_preview_is_bounded_without_truncating_readable_claim():
    text = "glacier " * 60
    record = project([revision("long", [claim(text)])])[0]["records"][1]
    assert len(record["preview"]) == 240 and record["preview_truncated"]
    assert json.loads(record["text"])["text"] == text


def test_search_returns_folded_type_state_preview_and_exact_read(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner chose glacier storage")
    raw = asyncio.run(server.memory.operate("agent", "sources", {}))["sources"]
    native = next(s for s in raw if s["kind"] == "conversation")
    endpoint = native["id"] + ":" + native["version"]
    item = claim()
    item["refs"] = [endpoint + ":0"]
    item["id"] = memory_contract.claim_id("decisions", item["text"], item["refs"])
    rows = [
        revision("leaf", [item]),
        revision("parent", [{"id": item["id"], "ref_revision": "leaf"}]),
    ]
    for row in rows:
        row["continuity"]["processed"] = {native["id"]: {}}
    monkeypatch.setattr(
        memory_records,
        "canonical",
        lambda *args: {"revisions": rows, "checkpoints": [], "links": []},
    )
    result = asyncio.run(server.memory.operate("agent", "search", {"q": ["glacier"]}))
    matches = [r for r in result["results"] if r["role"] == "derived_claim"]
    assert len(matches) == 2
    for match in matches:
        assert (match["kind"], match["state"], match["preview"]) == (
            "decision",
            "open",
            "Use glacier",
        )
        read = asyncio.run(
            server.memory.operate(
                "agent", "read", {"source": [match["source"]], "record": [match["record_id"]]}
            )
        )
        assert item["id"] in read["text"]
        assert endpoint + ":0" in read["text"]
