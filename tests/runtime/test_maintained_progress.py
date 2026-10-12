"""Maintained summaries consume new records once and remain separate from switch readiness."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from test_memory import memory_rig, transcript

from duckterm import memory_continuity, memory_spans, memory_summary
from duckterm.core import memory_folding
from duckterm.llm.summarizer import Summary

__all__ = ["memory_rig"]


def provider(monkeypatch):
    calls = []

    def generate(prompt):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            value = {
                "accept": [],
                "done_next_action_ids": [],
                "summary_validation": {"ready": True, "reason_codes": []},
            }
        else:
            data = json.loads(prompt.split("MEMORY UPDATE:\n", 1)[1])
            refs = [r["source"] + ":" + r["version"] + ":" + r["record"] for r in data["records"]]
            context = data.get("prior_context") or {
                "overview": "Continue the test work.",
                **{k: [] for k in memory_summary.FIELDS},
            }
            if refs:
                context["constraints"] = [
                    {
                        "text": "Retain the current owner constraints.",
                        "refs": refs,
                        **(
                            {"kind": "constraint", "state": "open"}
                            if "prior_context" not in data
                            else {}
                        ),
                    }
                ]
            value = {"summary": "Continue the test work.", "context": context}
        return SimpleNamespace(text=json.dumps(value))

    monkeypatch.setattr("duckterm.server.summarize", generate)
    return calls


def append(path, text):
    with path.open("a") as stream:
        stream.write(
            json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"
        )


def test_new_revision_processes_only_new_records_and_checkpoint_reuses(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "First owner constraint")
    calls = provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        assert first["continuity"]["remaining_records"] == 0
        assert first[
            "memory_sources"
        ]  # native originals retained; no duplicate session-card snapshot
        again = await server.progress_coordinator.refresh("agent")
        assert again["id"] == first["id"] and len(calls) == 2
        append(path, "New owner constraint")
        second = await server.progress_coordinator.refresh("agent")
        assert second["id"] != first["id"]
        data = json.loads(calls[2].split("MEMORY UPDATE:\n", 1)[1])
        assert [r["text"] for r in data["records"]] == ["New owner constraint"]
        assert "prior_context" not in data
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert cp["record"]["summary_ref"] == second["id"]
        assert len(calls) == 4
        assert server.digests.revision("agent", first["id"])["continuity"] == first["continuity"]

    asyncio.run(run())


def test_backlog_completes_bounded_spans_and_failure_preserves_completed_summary(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Older original")
    for n in range(100):
        append(path, f"Fact {n}: " + "x" * 1000)
    calls = provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        assert len(calls) > 2
        assert first["continuity"]["remaining_records"] == 0
        assert all(
            len(prompt.encode()) <= memory_spans.MAX_GENERATION_BYTES for prompt in calls[::2]
        )
        count = len(calls)
        second = await server.progress_coordinator.refresh("agent")
        assert second["id"] == first["id"] and len(calls) == count
        cached = server.history.session("agent")["progress"]
        append(path, "Next owner instruction")
        monkeypatch.setattr(
            "duckterm.server.summarize", lambda _: Summary("", "none", "provider_timeout")
        )
        failed = await server.progress_coordinator.refresh_result("agent")
        assert failed.state == "failed"
        assert server.history.session("agent")["progress"] == cached

    asyncio.run(run())


def test_rewritten_record_refuses_reuse_without_reciting_removed_claim(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Prior owner direction")
    calls = provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        transcript(tmp, "agent-claude", "Corrected direction")
        update = await server.progress_coordinator.refresh_result("agent")
        assert update.state == "failed" and update.reason == "source_changed"
        assert len(calls) == 2
        assert server.digests.revision("agent", first["id"]) is not None

    asyncio.run(run())


def test_turn_end_and_exit_never_spend_summary_tokens(memory_rig, monkeypatch):
    server, _, _, _ = memory_rig
    calls = []

    async def unexpected(key):
        calls.append(key)

    monkeypatch.setattr(server, "_refresh_progress", unexpected)

    async def run():
        for now in (2000000.0, 2000900.0, 3000000.0):
            monkeypatch.setattr("duckterm.server.time.time", lambda now=now: now)
            server._maybe_refresh_progress("agent")
            server._progress_on_exit("agent")
            await asyncio.sleep(0)
        assert calls == []

    asyncio.run(run())


def test_manual_checkpoint_joins_running_explicit_summary(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Preserve this owner constraint")
    calls = provider(monkeypatch)
    from duckterm import server as server_module

    original = server_module.summarize
    entered, released = threading.Event(), threading.Event()

    def paused(prompt):
        if not entered.is_set():
            entered.set()
            assert released.wait(3)
        return original(prompt)

    monkeypatch.setattr(server_module, "summarize", paused)

    async def run():
        automatic = asyncio.create_task(server.progress_coordinator.refresh("agent"))
        assert await asyncio.to_thread(entered.wait, 3)
        manual = asyncio.create_task(
            server._create_checkpoint("agent", server.history.session("agent"), "manual")
        )
        try:
            # Both requests use the same source identity; no second provider work.
            await asyncio.sleep(0.02)
            assert len(server.progress_coordinator.span_refreshes) == 1
        finally:
            released.set()
        revision, cp = await asyncio.gather(automatic, manual)
        assert cp["record"]["summary_ref"] == revision["id"] and len(calls) == 2

    asyncio.run(run())


def test_scheduled_exit_update_does_not_summarize_a_replacement_conversation(
    memory_rig, monkeypatch
):
    server, _, _, _ = memory_rig
    calls = []

    async def observe(key):
        calls.append(key)

    monkeypatch.setattr(server, "_refresh_progress", observe)

    async def run():
        server._progress_on_exit("agent")
        server.history.set_harness_identity(
            "agent",
            "codex",
            None,
            None,
            control={"native_binding": {"native_id": "replacement", "generation": "next"}},
        )
        await asyncio.sleep(0)
        assert calls == []

    asyncio.run(run())


def test_cold_start_preserves_chronology_then_processes_only_new_append(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Earlier direction: " + "x" * 30000)
    append(path, "Later correction: " + "y" * 30000)
    calls = provider(monkeypatch)

    async def run():
        await server.progress_coordinator.refresh("agent")
        assert len(calls) == 4
        assert "Earlier direction" in calls[0] and "Later correction" in calls[2]
        append(path, "New owner message after the first summary")
        await server.progress_coordinator.refresh("agent")
        data = json.loads(calls[4].split("MEMORY UPDATE:\n", 1)[1])
        assert [r["text"] for r in data["records"]] == ["New owner message after the first summary"]
        assert "prior_context" not in data

    asyncio.run(run())


def test_checkpoint_reports_updated_only_after_all_bounded_spans(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Original constraint")
    for n in range(100):
        append(path, f"Fact {n}: " + "x" * 1000)
    calls = provider(monkeypatch)

    async def run():
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert len(calls) > 2
        assert cp["summary_update"]["state"] == "updated"
        revision = server.digests.revision("agent", cp["record"]["summary_ref"])
        rows = memory_folding.rows(server.digests, "agent", revision["source"]["scope_hash"])
        assert sum(r["continuity"]["summarized_records"] for r in rows) == 101
        assert all(r["continuity"]["verified"] for r in rows)
        assert cp["handoff_eligible"]

    asyncio.run(run())


def test_manual_checkpoint_shares_explicit_failure_without_a_second_attempt(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Preserve this owner constraint")
    entered, released = threading.Event(), threading.Event()
    calls = []

    def failed(prompt):
        calls.append(prompt)
        entered.set()
        assert released.wait(3)
        return Summary("", "none", "provider_timeout")

    monkeypatch.setattr("duckterm.server.summarize", failed)

    async def run():
        coordinator = server.progress_coordinator
        automatic = asyncio.create_task(coordinator.refresh_result("agent"))
        assert await asyncio.to_thread(entered.wait, 3)
        manual_joined = asyncio.Event()
        original = asyncio.shield

        def observed(task):
            if task is coordinator.span_refreshes.get("agent") and asyncio.current_task() is manual:
                manual_joined.set()
            return original(task)

        monkeypatch.setattr(asyncio, "shield", observed)
        manual = asyncio.create_task(
            server._create_checkpoint("agent", server.history.session("agent"), "manual")
        )
        try:
            await asyncio.wait_for(manual_joined.wait(), 3)
        finally:
            released.set()
        outcome, cp = await asyncio.gather(automatic, manual)
        assert len(calls) == 1
        assert outcome.state == cp["summary_update"]["state"] == "failed"
        assert outcome.reason == cp["summary_update"]["reason"] == "provider_timeout"
        assert cp["record"]["summary_ref"] is None

    asyncio.run(run())


def test_large_notes_remain_current_work_instead_of_reentering_summary_input(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep the current owner constraint")
    calls = provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        server.history.set_meta("agent", notes="🔒" * 6500)
        outcome = await server.progress_coordinator.refresh_result("agent")
        assert outcome.state == "reused"
        assert len(calls) == 2
        assert "🔒" * 6500 not in "".join(calls)
        assert server.digests.revision("agent", first["id"])["continuity"] == first["continuity"]

    asyncio.run(run())


@pytest.mark.parametrize("task_count,mail_size", [(45, 0), (1, 13000), (45, 13000)])
def test_checkpoint_with_large_task_and_mail_inputs(memory_rig, monkeypatch, task_count, mail_size):
    from test_session_api import call

    server, _, peer, tmp = memory_rig
    transcript_path = transcript(
        tmp, "agent-claude", "Continue assigned work without losing owner decisions"
    )
    calls = provider(monkeypatch)
    for n in range(task_count):
        server.history.folder_tasks.start("Tests", "agent", f"Task {n}: " + "t" * 230)
    call(
        server.history,
        {**peer, "idempotency-key": "large-mail"},
        "POST",
        "/questions",
        {
            "target_session_id": "agent",
            "question": "Assignment: " + "m" * mail_size,
        },
    )

    async def run():
        captured = await server.progress_coordinator.capture("agent")
        assert len(captured["required"].encode()) < 6000
        pointers = json.loads(captured["required"])
        assert pointers["work_references"]["assigned_open_tasks"] == task_count
        assert pointers["work_references"]["incoming_messages"] == 1
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert cp["summary_update"]["state"] in {"updated", "partial"}
        revision = server.digests.revision("agent", cp["record"]["summary_ref"])
        if cp["summary_update"]["state"] == "partial":
            assert revision["continuity"]["pending_updates"]
            assert not revision["summary_validation"]["ready"]
            cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
            revision = server.digests.revision("agent", cp["record"]["summary_ref"])
        assert cp["summary_update"]["state"] == "updated"
        assert revision["span"] == "incremental"
        assert not revision["continuity"]["pending_updates"]
        for prompt in calls[::2]:
            data = json.loads(prompt.split("MEMORY UPDATE:\n", 1)[1])
            assert len(json.dumps(data["records"]).encode()) < memory_continuity.MAX_UPDATE_BYTES
            if mail_size:
                assert "m" * mail_size not in prompt
        assert len(server.history.folder_tasks.list_tasks()) == task_count
        cached = server.history.session("agent")["progress"]
        append(transcript_path, "New instruction")
        monkeypatch.setattr(
            "duckterm.server.summarize", lambda _: Summary("", "none", "provider_timeout")
        )
        failed = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert failed["summary_update"]["state"] == "failed"
        assert failed["summary_update"]["revision_id"] is None
        assert failed["record"]["summary_ref"] == revision["id"]
        assert server.history.session("agent")["progress"] == cached

    asyncio.run(run())


def test_new_oversized_mail_refreshes_boundary_without_summarizing_body(memory_rig, monkeypatch):
    from test_session_api import call

    server, _, peer, tmp = memory_rig
    transcript(tmp, "agent-claude", "Original owner instruction")
    provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh_result("agent")
        assert first.state == "updated"
        # API-valid input expands beyond the serialized source-record budget.
        call(
            server.history,
            {**peer, "idempotency-key": "escaped-mail"},
            "POST",
            "/questions",
            {
                "target_session_id": "agent",
                "question": "\x01" * 10000,
            },
        )
        second = await server.progress_coordinator.refresh_result("agent")
        assert second.state == "reused"
        assert second.revision["id"] == first.revision["id"]
        assert not second.revision["continuity"]["pending_updates"]
        assert not second.revision["continuity"]["gaps"]
        assert second.revision["span"] == "incremental"

    asyncio.run(run())
