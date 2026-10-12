"""Maintained handoffs must work when neither summarizer can answer."""

import asyncio
import json

from tests.runtime import test_memory_preparation as fixtures

memory_rig = fixtures.memory_rig
preparation = fixtures.preparation


def test_first_handoff_requires_a_summary_and_retains_source_when_provider_unavailable(
    preparation, monkeypatch
):
    from duckterm.llm.summarizer import Summary

    server, calls, _ = preparation
    monkeypatch.setattr(
        "duckterm.server.summarize", lambda _: Summary("", "none", "provider_timeout")
    )
    result = asyncio.run(fixtures.prepare(server))
    assert result["state"] == "failed"
    assert "provider_timeout" in result["reason"]
    assert "proof" not in result
    assert not server.history.session("agent").get("progress")
    assert not server.history.checkpoints("agent")
    assert server.history.session("agent")["runtime"] == "claude-code"


def test_existing_complete_spans_prepare_without_model_or_raw_tail(preparation, monkeypatch):
    from test_maintained_progress import append

    server, _, tmp = preparation
    from test_maintained_progress import provider

    calls = provider(monkeypatch)
    path = fixtures.transcript(tmp, "agent-claude", "older original " + "x" * 22000)
    append(path, "recent original " + "y" * 22000)

    async def run():
        await server.progress_coordinator.refresh("agent")
        assert len(calls) == 4

        def forbidden(*args):
            raise AssertionError("Complete summaries must be reused")

        monkeypatch.setattr("duckterm.server.summarize", forbidden)
        result = await fixtures.prepare(server)
        assert result["state"] == "ready", result
        detail = server.memory_preparation.details("agent", result["preparation_id"])
        text = detail["brief"]["text"]
        assert "duckterm memory read" in text and "x" * 100 not in text
        assert result["coverage"]["handoff"]["protected_kinds"]["constraint"] == 2
        assert len(text.encode()) <= 32000

    asyncio.run(run())


def test_prepare_reuses_verified_revision_and_refuses_rewritten_evidence(preparation, monkeypatch):
    from test_maintained_progress import provider

    server, _, tmp = preparation
    calls = provider(monkeypatch)

    async def run():
        revision = await server.progress_coordinator.refresh("agent")
        assert revision["continuity"]["verified"]
        ready = await fixtures.prepare(server)
        assert len(calls) == 2  # Opening the switch never generates another revision.
        assert ready["proof"]["revision_id"] == revision["id"]
        # Reference-only originals are still not summarized; keep that coverage honest.
        assert ready["coverage"]["handoff"]["summary_state"] == "current"
        assert revision["continuity"]["remaining_records"] == 0
        assert ready["coverage"]["covered_source_count"] > 0
        fixtures.transcript(tmp, "agent-claude", "Owner replaced the prior instructions")
        next_ready = await fixtures.prepare(server, "new-dialog")
        assert next_ready["state"] == "stale_source"
        assert "proof" not in next_ready
        assert len(calls) == 2

    asyncio.run(run())


def test_onboarding_identifies_named_session_directory_and_selected_target(preparation):
    server, calls, tmp = preparation
    server.history.set_meta("agent", name="Startup · video")
    view = asyncio.run(fixtures.prepare(server))
    assert view["state"] == "ready"
    detail = server.memory_preparation.details("agent", view["preparation_id"])
    text = detail["brief"]["text"]
    launch = json.loads(text.split("Launch context:\n", 1)[1].split("\n", 1)[0])
    assert launch == {
        "session_key": "agent",
        "session_name": "Startup · video",
        "cwd": str(tmp),
        "source_harness": "claude-code",
        "target": {"harness": "codex", "model": {"mode": "explicit", "id": "selected-model"}},
    }
    assert len(calls) == 2 and len(text.encode()) <= 32000
