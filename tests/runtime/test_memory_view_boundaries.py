import asyncio

from test_maintained_progress import append, provider
from test_memory import memory_rig, transcript
from test_memory_view import get

from duckterm import memory_continuity, memory_lifecycle

__all__ = ["memory_rig"]


def test_current_view_excludes_completed_but_unpublished_leaf(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Published owner constraint")
    calls = provider(monkeypatch)

    async def scenario():
        published = await server.progress_coordinator.refresh("agent")
        append(path, "New work in an incomplete checkpoint")
        captured = await server.progress_coordinator.capture("agent")
        captured = await memory_continuity.capture(server, "agent", captured)
        update, _ = await server.progress_coordinator.write_span(
            "agent", captured, published["span_input"]["next_boundary"]
        )
        assert update.state == "updated"
        return published, update.revision

    published, staged = asyncio.run(scenario())
    count = len(calls)
    status, view = get(server)
    assert status == 200
    assert view["current_revision_id"] == published["id"]
    assert view["memory"]["span_count"] == 1
    staged_claim = staged["continuity"]["context"]["constraints"][0]["id"]
    assert all(c["id"] != staged_claim for g in view["memory"]["groups"] for c in g["claims"])
    status, historical = get(server, revision=staged["id"])
    assert status == 200 and historical["revision"]["id"] == staged["id"]
    assert "memory" not in historical
    assert len(calls) == count


def test_missing_transition_evidence_never_reports_claim_open():
    claim = {"id": "claim", "state": "open"}
    link = {
        "id": "transition",
        "lifecycle_version": 1,
        "target_id": "claim",
        "replacement_id": None,
        "relation": "claim_retired",
        "evidence": [{"source": "a" * 32 + ":" + "b" * 64, "record": "0"}],
    }
    state = memory_lifecycle.effective(claim, [link], [])
    assert state["saved_state"] == "open"
    assert state["state"] == "unknown"
    assert state["state_reason"] == "transition_evidence_unavailable"
    assert claim == {"id": "claim", "state": "open"}
