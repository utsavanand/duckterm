import asyncio
import json

from test_memory import memory_rig
from test_memory_assembly import rows
from test_memory_lifecycle import setup, write
from test_memory_spans import snapshot

from duckterm import memory_assembly, memory_brief, memory_spans

__all__ = ["memory_rig"]


def test_retired_claim_search_exposes_effective_state(memory_rig, monkeypatch):
    server, headers, _, revision, body = setup(memory_rig, monkeypatch)
    assert write(server, headers, body)[0] == 200
    catalog = server.memory.catalog("agent", retrieval=True)
    sources, gaps = server.memory.materialize(catalog)
    view = memory_assembly.assemble(
        [revision], links=catalog["canonical"]["links"], sources=sources
    )
    assert view["context"]["constraints"][0]["state"] == "retired"
    text = revision["continuity"]["context"]["constraints"][0]["text"]
    found = asyncio.run(server.memory.operate("agent", "search", {"q": [text]}))
    claims = [r for r in found["results"] if r["role"] == "derived_claim"]
    assert claims
    assert {r["state"] for r in claims} == {"retired"}
    assert {r["saved_state"] for r in claims} == {"open"}
    original = asyncio.run(
        server.memory.operate(
            "agent",
            "read",
            {
                "source": [claims[0]["source"]],
                "record": [claims[0]["record_id"]],
            },
        )
    )
    assert original["current_claim_states"][0]["state"] == "retired"
    assert json.loads(original["text"].split(": ", 1)[1])["state"] == "open"


def test_generation_input_preserves_32kb_response_reserve():
    captured = snapshot(["x" * 20000])
    plan = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
    assert len(memory_spans.prompt(plan).encode()) <= 16000


def test_handoff_counts_items_by_taxonomy_kind_and_category():
    revisions = rows(3)
    kinds = ["constraint", "callout", "learning"]
    sources = []
    for row, kind in zip(revisions, kinds, strict=False):
        claim = row["continuity"]["context"]["constraints"][0]
        claim["kind"] = kind
        for ref in claim["refs"]:
            source, version, record = ref.split(":", 2)
            sources.append(
                {
                    "id": source,
                    "version": version,
                    "kind": "session",
                    "records": [{"id": record, "role": "session", "text": "evidence"}],
                }
            )
    _, coverage, _ = memory_brief.assemble("test", sources, "{}", revisions, None)
    # A legacy callout is read as a constraint under the 2026-10-30 taxonomy.
    assert coverage["protected_kinds"]["constraint"] == 2
    assert coverage["protected_kinds"]["learning"] == 1
    assert coverage["protected_categories"] == {"intent": 0, "rules": 2, "facts": 1}


def test_supersede_retry_remains_idempotent_after_replacement_retirement(memory_rig, monkeypatch):
    from test_maintained_progress import append
    from test_memory_graph import ref, sources

    server, headers, _, first, body = setup(memory_rig, monkeypatch)
    path = next(memory_rig[3].rglob("agent-claude.jsonl"))
    append(path, "Owner replaces the old constraint with a new constraint.")
    second = asyncio.run(server.progress_coordinator.refresh("agent"))
    catalog = sources(server)
    replacement = next(s for s in catalog if s.get("revision_id") == second["id"])
    native = next(s for s in catalog if s["kind"] == "conversation")
    body.update(
        operation="supersede",
        replacement=ref(replacement, "constraints:0"),
        replacement_id=second["continuity"]["context"]["constraints"][0]["id"],
        evidence=[ref(native, "1")],
        request_key="supersede",
    )
    assert write(server, headers, body)[0] == 200
    retire = {
        **body,
        "operation": "retire",
        "target": body["replacement"],
        "target_id": body["replacement_id"],
        "replacement": None,
        "replacement_id": None,
        "request_key": "retire-replacement",
    }
    assert write(server, headers, retire)[0] == 200
    status, replay = write(server, headers, body)
    assert status == 200 and replay["replayed"], (status, replay)
