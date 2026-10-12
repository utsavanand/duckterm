"""Checkpoint spans are independently written and collectively readable."""

import asyncio
import threading

import pytest
from test_maintained_progress import append
from test_memory import memory_rig, transcript
from test_memory_spans import provider

from duckterm import memory_continuity, memory_handoff
from duckterm.core import memory_folding
from duckterm.persistence.saved_state import current_revision

__all__ = ["memory_rig"]


def test_checkpoint_completes_bounded_spans_and_second_write_reads_only_gap(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Initial constraint " + "x" * 22000)
    append(path, "Second instruction " + "y" * 22000)
    calls, _ = provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh_result("agent")
        assert first.state == "updated"
        assert len(calls) == 4
        assert "Initial constraint" in calls[0]
        assert "Initial constraint" not in calls[2]
        assert "Second instruction" in calls[2]
        append(path, "Third instruction")
        second = await server.progress_coordinator.refresh_result("agent")
        assert second.state == "updated" and len(calls) == 6
        assert "Third instruction" in calls[4]
        assert "Second instruction" not in calls[4]
        assert "prior_context" not in calls[4]
        captured = await server.progress_coordinator.capture("agent")
        captured = await memory_continuity.capture(server, "agent", captured)
        rows = memory_folding.rows(server.digests, "agent", captured["source"]["scope_hash"])
        from duckterm import memory_records

        evidence_sources, gaps = memory_records.retained(
            server.memory.catalog("agent"), captured["memory_sources"]
        )
        assert not gaps
        brief, coverage, included = memory_handoff.assemble(
            "agent",
            captured["memory_sources"],
            captured["required"],
            second.revision,
            revisions=rows,
            evidence_sources=evidence_sources,
        )
        assert len(rows) == 3
        assert coverage["protected_kinds"]["constraints"] == 3
        assert "duckterm memory read" in brief
        assert "x" * 100 not in brief  # no raw transcript tail in a span brief
        assert included == []
        again = await server.progress_coordinator.refresh_result("agent")
        assert again.state == "reused" and len(calls) == 6

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["native_mutation", "capture_conflict"])
def test_final_publication_refuses_changed_sources_but_retains_finished_leaf(
    memory_rig, monkeypatch, failure
):
    from duckterm.core import memory_refresh
    from duckterm.core.session_api import APIError

    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Initial owner constraint")
    provider(monkeypatch)
    coordinator = server.progress_coordinator
    write = coordinator.write_span
    completed = False

    async def completed_write(*args):
        nonlocal completed
        result = await write(*args)
        completed = True
        return result

    monkeypatch.setattr(coordinator, "write_span", completed_write)
    capture = memory_continuity.capture

    async def final_capture(*args):
        if completed and failure == "capture_conflict":
            raise APIError(409, "Changed while reading")
        return await capture(*args)

    monkeypatch.setattr(memory_continuity, "capture", final_capture)
    current = memory_refresh.current

    def final_check(*args, **kwargs):
        if kwargs.get("catalog") and failure == "native_mutation":
            append(path, "New work at publication boundary")
        return current(*args, **kwargs)

    monkeypatch.setattr(memory_refresh, "current", final_check)

    async def run():
        result = await coordinator.refresh_result("agent")
        assert result.state == "failed" and result.reason == "source_changed"
        assert current_revision(server.history.session("agent")) is None
        saved = server.history._conn.execute(
            "SELECT count(*) FROM digest_items WHERE session_key='agent' "
            "AND bucket='summary_revision_v1'"
        ).fetchone()[0]
        assert saved == 1

    asyncio.run(run())


def test_different_capture_cannot_join_an_older_update(memory_rig, monkeypatch):
    from duckterm import server as server_module

    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Initial owner constraint")
    calls, _ = provider(monkeypatch)
    original = server_module.summarize
    entered, released = threading.Event(), threading.Event()

    def paused(prompt):
        entered.set()
        assert released.wait(3)
        return original(prompt)

    monkeypatch.setattr(server_module, "summarize", paused)

    async def run():
        first = asyncio.create_task(server.progress_coordinator.refresh_result("agent"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            append(path, "Different native capture without a hook event")
            second = await server.progress_coordinator.refresh_result("agent")
            assert second.state == "failed" and second.diagnostic == "different_active_capture"
        finally:
            released.set()
        result = await first
        assert result.state == "failed"
        assert len(calls) <= 2
        assert current_revision(server.history.session("agent")) is None

    asyncio.run(run())


def test_failed_later_batch_keeps_boundary_and_retry_does_not_repeat_first(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "First " + "x" * 22000)
    append(path, "Second " + "y" * 22000)
    calls, fail = provider(monkeypatch)
    real_write = server.progress_coordinator.write_span

    async def write(key, captured, lower):
        if any(value["count"] for value in lower["sources"].values()):
            fail[0] = True
        return await real_write(key, captured, lower)

    monkeypatch.setattr(server.progress_coordinator, "write_span", write)

    async def run():
        failed = await server.progress_coordinator.refresh_result("agent")
        assert failed.state == "failed" and failed.reason == "provider_timeout"
        assert current_revision(server.history.session("agent")) is None
        assert len(calls) == 3
        fail[0] = False
        monkeypatch.setattr(server.progress_coordinator, "write_span", real_write)
        result = await server.progress_coordinator.refresh_result("agent")
        assert result.state == "updated" and len(calls) == 5
        assert "First " not in calls[3]
        assert current_revision(server.history.session("agent")) == result.revision["id"]

    asyncio.run(run())
