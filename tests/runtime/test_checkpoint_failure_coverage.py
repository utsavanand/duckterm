"""A failed checkpoint reports durable batches without promoting partial memory."""

import asyncio
import json

import pytest
from test_maintained_progress import append
from test_memory import memory_rig, transcript
from test_memory_spans import provider

from duckterm.checkpoint_clock import project
from duckterm.core import memory_refresh
from duckterm.persistence.saved_state import current_revision

__all__ = ["memory_rig"]


@pytest.mark.parametrize("failing_batch", [1, 3])
def test_failed_checkpoint_persists_coverage_and_retry_only_writes_remaining(
    memory_rig, monkeypatch, failing_batch
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Earlier owner requirement")
    calls, fail = provider(monkeypatch)
    coordinator = server.progress_coordinator
    write = coordinator.write_span
    batch = 0

    async def failing_write(*args):
        nonlocal batch
        batch += 1
        fail[0] = batch == failing_batch
        return await write(*args)

    async def checkpoint():
        return await server._create_checkpoint("agent", server.history.session("agent"), "manual")

    async def run():
        good = await checkpoint()
        previous = current_revision(server.history.session("agent"))
        previous_progress = server.history.session("agent")["progress"]
        assert previous and good["summary_update"]["state"] == "updated"
        # The real byte selector admits one of these records per batch.
        # No budget overrides, provider traffic, or real session data.
        for number in range(20):
            append(path, f"New owner requirement {number}: " + "x" * 11000)
        monkeypatch.setattr(coordinator, "write_span", failing_write)
        failed = await checkpoint()
        update = failed["summary_update"]
        assert update["state"] == "failed" and update["reason"] == "provider_timeout"
        assert update["revision_id"] is None
        coverage = update["span_coverage"]
        completed = failing_batch - 1
        assert coverage == {
            "state": "incomplete",
            "scope": "refresh_attempt",
            "revision_ids": coverage["revision_ids"],
            "available_records": 20,
            "eligible_records": 20,
            "summarized_records": completed,
            "pending_records": 20 - completed,
            "reference_only_records": 0,
        }
        assert len(set(coverage["revision_ids"])) == completed
        assert previous not in coverage["revision_ids"]
        for identity in coverage["revision_ids"]:
            revision = server.digests.revision("agent", identity)
            assert revision["summary_review"]["state"] == "accepted"
            assert revision["continuity"]["summarized_records"] == 1
        # Counts and links survive the checkpoint's DB/JSON projection.
        stored = server.history._conn.execute(
            "SELECT record_json FROM checkpoints WHERE id=?", (failed["id"],)
        ).fetchone()
        assert json.loads(stored[0])["summary_update"] == update
        assert current_revision(server.history.session("agent")) == previous
        assert server.history.session("agent")["progress"] == previous_progress
        assert not failed["handoff_eligible"]
        assert project(server.history.checkpoints("agent"))["checkpoint_id"] == good["id"]

        before_retry = len(calls)
        fail[0] = False
        monkeypatch.setattr(coordinator, "write_span", write)
        retried = await checkpoint()
        assert retried["summary_update"]["state"] == "updated"
        assert retried["summary_update"]["span_coverage"]["pending_records"] == 0
        writer_records = [
            record["text"]
            for prompt in calls[before_retry:]
            if not prompt.startswith("You are validating")
            for record in json.loads(prompt.split("MEMORY UPDATE:\n", 1)[1])["records"]
        ]
        assert writer_records == [
            f"New owner requirement {number}: " + "x" * 11000 for number in range(completed, 20)
        ]
        assert len(calls) - before_retry == 2 * (20 - completed)
        assert project(server.history.checkpoints("agent"))["checkpoint_id"] == retried["id"]
        # A retry cannot retroactively turn the original failed attempt into success.
        original = next(
            cp for cp in server.history.checkpoints("agent") if cp["id"] == failed["id"]
        )
        assert original["summary_update"] == update

    asyncio.run(run())


def test_waiter_source_change_keeps_only_this_attempts_accepted_refs(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "An earlier owner requirement")
    calls, _ = provider(monkeypatch)
    coordinator = server.progress_coordinator
    original_run = memory_refresh._run

    async def changed_after_publication(*args):
        result = await original_run(*args)
        assert result.state == "updated"
        append(path, "Later work arriving before the waiting caller resumes")
        return result

    async def run():
        earlier = await coordinator.refresh_result("agent")
        assert earlier.state == "updated"
        assert earlier.metadata(1)["attempt_coverage"]["state"] == "complete"
        assert earlier.metadata(1)["attempt_coverage"]["revision_ids"] == [earlier.revision["id"]]
        append(path, "This attempt's owner requirement")
        monkeypatch.setattr(memory_refresh, "_run", changed_after_publication)
        result = await coordinator.refresh_result("agent")
        assert result.state == "failed"
        assert result.diagnostic == "refresh_result:native_file_changed"
        assert result.metadata(2)["revision_id"] is None
        coverage = result.metadata(2)["span_coverage"]
        assert result.metadata(2)["attempt_coverage"] == coverage
        assert coverage["state"] == "incomplete" and coverage["scope"] == "refresh_attempt"
        assert coverage["eligible_records"] == coverage["summarized_records"] == 1
        assert coverage["pending_records"] == 0
        assert len(coverage["revision_ids"]) == 1
        assert earlier.revision["id"] not in coverage["revision_ids"]
        saved = server.digests.revision("agent", coverage["revision_ids"][0])
        assert saved["summary_review"]["state"] == "accepted"
        # Publication already happened before this late fence; the report must
        # not imply rollback or that the summary covers the just-appended work.
        assert current_revision(server.history.session("agent")) == saved["id"]
        monkeypatch.setattr(memory_refresh, "_run", original_run)
        before_retry = len(calls)
        retried = await coordinator.refresh_result("agent")
        assert retried.state == "updated"
        assert len(calls) - before_retry == 2
        records = json.loads(calls[before_retry].split("MEMORY UPDATE:\n", 1)[1])["records"]
        assert [record["text"] for record in records] == [
            "Later work arriving before the waiting caller resumes"
        ]

    asyncio.run(run())
