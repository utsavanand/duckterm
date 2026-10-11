"""Historical backlog is not a failed update; skipped new work still matters."""

import asyncio
import json
from types import SimpleNamespace

from test_maintained_progress import append, provider
from test_memory import memory_rig, transcript

from duckterm.core.saved_progress import SummaryUpdate

__all__ = ["memory_rig"]


def test_large_history_saves_usable_context_with_resolvable_evidence(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Earlier conversation")
    for number in range(5001):
        append(path, f"Historical record {number}: " + "x" * 100)
    append(path, "Owner: build export; keep files private; use JSON; next run export checks.")

    def generate(prompt):
        if prompt.startswith("You are validating"):
            value = {
                "accept": [],
                "done_next_action_ids": [],
                "summary_validation": {"ready": True, "reason_codes": []},
            }
        else:
            rows = json.loads(prompt.split("MEMORY UPDATE:\n", 1)[1])["records"]
            row = next(r for r in rows if r["text"].startswith("Owner: build export"))
            ref = row["ref"]
            value = {
                "summary": "Build the private JSON export; next run its checks.",
                "context": {
                    "overview": "Continue the private JSON export.",
                    **{
                        field: [{"text": text, "refs": [ref]}]
                        for field, text in {
                            "goals": "Build export",
                            "constraints": "Keep files private",
                            "decisions": "Use JSON",
                            "unfinished": "Run export checks",
                        }.items()
                    },
                    "questions": [],
                    "risks": [],
                },
            }
        return SimpleNamespace(text=json.dumps(value))

    monkeypatch.setattr("duckterm.server.summarize", generate)

    async def run():
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert cp["summary_update"]["state"] == "updated"
        revision = server.digests.revision("agent", cp["record"]["summary_ref"])
        continuity = revision["continuity"]
        assert continuity["available_records"] > 5000 and continuity["remaining_records"] > 0
        assert continuity["verified"] and continuity["update_complete"]
        for field in ("goals", "constraints", "decisions", "unfinished"):
            assert continuity["context"][field]
            for claim in continuity["context"][field]:
                assert claim["text"].strip() and claim["refs"]
                for ref in claim["refs"]:
                    source, version, record = ref.split(":", 2)
                    found = await server.memory.operate(
                        "agent", "read", {"source": [source + ":" + version], "record": [record]}
                    )
                    assert "Owner: build export" in found["text"]
        return revision

    asyncio.run(run())


def test_skipped_new_work_survives_advancing_observation_frontiers(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Original direction")
    provider(monkeypatch)

    async def run():
        await server.progress_coordinator.refresh("agent")
        append(path, "New owner constraint: " + "a" * 30000)
        append(path, "New result: " + "b" * 30000)
        first = await server.progress_coordinator.refresh_result("agent")
        assert first.state == "partial"
        pending = first.revision["continuity"]["pending_updates"]
        assert sum(map(len, pending.values())) == 1
        append(path, "Another new result: " + "c" * 30000)
        second = await server.progress_coordinator.refresh_result("agent")
        assert second.state == "partial"
        assert second.revision["continuity"]["pending_updates"] == pending
        third = await server.progress_coordinator.refresh_result("agent")
        assert third.state == "updated"
        assert not third.revision["continuity"]["pending_updates"]
        assert third.revision["continuity"]["update_complete"]

    asyncio.run(run())


def test_oversized_latest_record_stays_partial(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "A small retained fact")
    append(path, "Latest owner constraints " + "x" * 60000)
    provider(monkeypatch)

    async def run():
        result = await server.progress_coordinator.refresh_result("agent")
        assert result.state == "partial"
        assert result.revision["continuity"]["gaps"]
        assert not result.revision["continuity"]["update_complete"]

    asyncio.run(run())


def test_old_revision_is_not_recertified_or_other_failures_hidden():
    old = {
        "continuity": {"verified": True, "remaining_records": 9000, "gaps": []},
        "summary_validation": {"ready": False, "reason_codes": ["summary_coverage_partial"]},
    }
    assert SummaryUpdate.from_revision(old).state == "partial"
    changed = {
        **old,
        "continuity": {**old["continuity"], "update_complete": True},
        "summary_validation": {"ready": False, "reason_codes": ["source_changed"]},
    }
    assert SummaryUpdate.from_revision(changed).state == "failed"
