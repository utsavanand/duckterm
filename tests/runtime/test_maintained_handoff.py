"""Maintained handoffs must work when neither summarizer can answer."""

import asyncio
import json

from tests.runtime import test_memory_preparation as fixtures

from duckterm.core.session_api import APIError

memory_rig = fixtures.memory_rig
preparation = fixtures.preparation


def test_prepare_without_summary_or_provider_keeps_explicit_coverage(preparation, monkeypatch):
    server, calls, _ = preparation

    async def exhausted(*args, **kwargs):
        raise APIError(503, "Quota exhausted")

    def unavailable(*args, **kwargs):
        raise AssertionError("Switch preparation must not invoke a summarizer")

    monkeypatch.setattr("duckterm.memory_provider.generate", exhausted)
    monkeypatch.setattr("duckterm.server.summarize", unavailable)
    result = asyncio.run(fixtures.prepare(server))
    assert result["state"] == "ready", result
    assert result["coverage"]["available_text"] == "not_processed"
    assert result["coverage"]["handoff"]["summary_state"] == "unavailable"
    assert result["proof"]["revision_id"] is None
    assert calls == []
    detail = server.memory_preparation.details("agent", result["preparation_id"])
    assert "retain glacier originals" in detail["brief"]["text"]
    assert "duckterm memory read" in detail["brief"]["text"]
    assert "duckterm session task update" in detail["brief"]["text"]
    assert "No generated summary" in detail["brief"]["text"]
    assert len(detail["brief"]["text"].encode()) <= 32000
    assert not server.history.session("agent").get("progress")
    cp = server.history.checkpoints("agent")[0]
    assert cp["record"]["summary_ref"] is None
    assert cp["record"]["memory_sources"]
    assert cp["record"]["handoff"]["method"] == "maintained"


def test_long_history_preparation_includes_whole_recent_records_without_model(
    preparation, monkeypatch
):
    server, _, tmp = preparation
    native = fixtures.transcript(tmp, "agent-claude", "old secret archive detail")
    with native.open("a") as stream:
        for n in range(250):
            stream.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": f"record-{n}: " + "x" * 8000,
                        },
                    }
                )
                + "\n"
            )
        stream.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "Continue with startup only; leave Product unchanged.",
                    },
                }
            )
            + "\n"
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError("No full-history model pass at handoff")

    monkeypatch.setattr("duckterm.memory_provider.generate", forbidden)
    result = asyncio.run(fixtures.prepare(server))
    assert result["state"] == "ready", result
    coverage = result["coverage"]["handoff"]
    assert coverage["omitted_records"] > 200
    detail = server.memory_preparation.details("agent", result["preparation_id"])
    text = detail["brief"]["text"]
    assert "leave Product unchanged" in text
    assert "old secret archive detail" not in text
    assert "not all included" in text
    assert len(text.encode()) <= 32000


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
        assert ready["coverage"]["handoff"]["summary_state"] == "current"
        assert ready["coverage"]["covered_source_count"] > 0
        fixtures.transcript(tmp, "agent-claude", "Owner replaced the prior instructions")
        next_ready = await fixtures.prepare(server, "new-dialog")
        assert next_ready["state"] == "ready"
        assert next_ready["proof"]["revision_id"] is None
        detail = server.memory_preparation.details("agent", next_ready["preparation_id"])
        assert "Retain the current owner constraints" not in detail["brief"]["text"]
        assert "Owner replaced the prior instructions" in detail["brief"]["text"]
        assert len(calls) == 2

    asyncio.run(run())
