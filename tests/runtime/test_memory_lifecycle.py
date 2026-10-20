"""Explicit lifecycle authority, evidence retention and handoff projection."""

import asyncio
import json

import pytest
from test_memory import memory_rig, transcript
from test_memory_graph import ref, sources
from test_memory_spans import provider
from test_session_api import dispatch

from duckterm import memory_assembly, memory_lifecycle
from duckterm.core import memory_folding
from duckterm.core.session_api import APIError

__all__ = ["memory_rig"]


def setup(memory_rig, monkeypatch):
    server, headers, peer_headers, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner explicitly withdraws the glacier constraint.")
    provider(monkeypatch)
    revision = asyncio.run(server.progress_coordinator.refresh("agent"))
    catalog = sources(server)
    summary = next(s for s in catalog if s["kind"] == "revision")
    native = next(s for s in catalog if s["kind"] == "conversation")
    body = {
        "operation": "retire",
        "target": ref(summary, "constraints:0"),
        "target_id": revision["continuity"]["context"]["constraints"][0]["id"],
        "replacement": None,
        "replacement_id": None,
        "evidence": [ref(native)],
        "request_key": "retire-glacier",
    }
    return server, headers, peer_headers, revision, body


def write(server, headers, body):
    return dispatch(
        server, "POST", "/api/v1/session/memory/claim", headers, json.dumps(body).encode()
    )


def test_own_claim_retirement_is_immutable_idempotent_and_applies_to_assembly(
    memory_rig, monkeypatch
):
    server, headers, _, revision, body = setup(memory_rig, monkeypatch)
    status, reply = write(server, headers, body)
    assert status == 200, reply
    assert reply["relation"]["provenance"]["authority"] == "owning_session"
    assert write(server, headers, body)[1]["replayed"]
    assert server.digests.revision("agent", revision["id"])["continuity"] == revision["continuity"]
    catalog = server.memory.catalog("agent", retrieval=True)
    original, gaps = server.memory.materialize(catalog)
    assert not gaps
    rows = memory_folding.rows(server.digests, "agent", revision["source"]["scope_hash"])
    view = memory_assembly.assemble(rows, links=catalog["canonical"]["links"], sources=original)
    assert view["context"]["constraints"][0]["state"] == "retired"
    with pytest.raises(APIError):
        memory_assembly.assemble(rows, links=catalog["canonical"]["links"], sources=[])


@pytest.mark.parametrize("fault", ["peer", "actor", "missing", "derived", "target", "replacement"])
def test_unauthorized_or_unevidenced_transition_is_refused(memory_rig, monkeypatch, fault):
    server, headers, peer, revision, body = setup(memory_rig, monkeypatch)
    if fault == "peer":
        headers = peer
    elif fault == "actor":
        body["actor"] = "owner"
    elif fault == "missing":
        body["evidence"] = []
    elif fault == "derived":
        body["evidence"] = [body["target"]]
    elif fault == "target":
        body["target_id"] = "0" * 16
    elif fault == "replacement":
        body["operation"] = "supersede"
    status, _ = write(server, headers, body)
    assert status in {400, 403, 404, 409}
    assert not server.memory.catalog("agent", retrieval=True)["canonical"]["links"]
    assert server.digests.revision("agent", revision["id"])["continuity"] == revision["continuity"]


def test_owner_route_uses_owner_auth_and_peer_token_cannot_fall_through(memory_rig, monkeypatch):
    server, _, peer, _, body = setup(memory_rig, monkeypatch)
    path = "/sessions/agent/memory-claim"
    assert dispatch(server, "POST", path, peer, json.dumps(body).encode())[0] == 403
    assert dispatch(server, "POST", path, {}, json.dumps(body).encode())[0] == 401
    status, reply = dispatch(
        server, "POST", path, {"x-duckterm-token": server.token}, json.dumps(body).encode()
    )
    assert status == 200, reply
    assert reply["relation"]["provenance"]["authority"] == "owner"


def test_old_annotations_are_inert_and_cycles_are_rejected():
    assert memory_lifecycle.transitions([{"relation": "supersedes"}]) == {}
    links = [
        {"id": "one", "lifecycle_version": 1, "target_id": "a", "replacement_id": "b"},
        {"id": "two", "lifecycle_version": 1, "target_id": "b", "replacement_id": "a"},
    ]
    with pytest.raises(ValueError, match="supersession_cycle"):
        memory_lifecycle.transitions(links)


def test_supersession_preserves_originals_and_cannot_reverse_or_close_twice(
    memory_rig, monkeypatch
):
    from test_maintained_progress import append

    from duckterm import memory_brief

    server, headers, _, first, body = setup(memory_rig, monkeypatch)
    # Resolve the known synthetic transcript path without reading any real session.
    path = memory_rig[3] / ".claude" / "projects"
    files = list(path.rglob("agent-claude.jsonl"))
    if not files:
        files = list(memory_rig[3].rglob("agent-claude.jsonl"))
    assert len(files) == 1
    append(files[0], "Owner replaces the glacier constraint with the new constraint.")
    second = asyncio.run(server.progress_coordinator.refresh("agent"))
    catalog = sources(server)
    replacement = next(s for s in catalog if s.get("revision_id") == second["id"])
    current_native = next(s for s in catalog if s["kind"] == "conversation")
    body.update(
        operation="supersede",
        replacement=ref(replacement, "constraints:0"),
        replacement_id=second["continuity"]["context"]["constraints"][0]["id"],
        evidence=[ref(current_native, "1")],
        request_key="replace-glacier",
    )
    status, reply = write(server, headers, body)
    assert status == 200, reply
    assert write(server, headers, body)[1]["replayed"]
    full = server.memory.catalog("agent", retrieval=True)
    original, gaps = server.memory.materialize(full)
    assert not gaps
    rows = memory_folding.rows(server.digests, "agent", second["source"]["scope_hash"])
    links = full["canonical"]["links"]
    view = memory_assembly.assemble(rows, links=links, sources=original)
    assert [c["state"] for c in view["context"]["constraints"]] == ["superseded", "open"]
    brief, _, _ = memory_brief.assemble("agent", original, "{}", rows, None, links=links)
    assert body["target_id"] not in brief
    assert body["replacement_id"] in brief
    assert server.digests.revision("agent", first["id"])["continuity"] == first["continuity"]
    reverse = {
        **body,
        "target": body["replacement"],
        "target_id": body["replacement_id"],
        "replacement": body["target"],
        "replacement_id": body["target_id"],
        "request_key": "reverse",
    }
    assert write(server, headers, reverse)[0] == 409
    assert write(server, headers, {**body, "request_key": "again"})[0] == 409
