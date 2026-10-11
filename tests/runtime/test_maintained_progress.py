"""Maintained summaries consume new records once and remain separate from switch readiness."""

import asyncio
import json
import threading
from types import SimpleNamespace

from test_memory import memory_rig, transcript

from duckterm import memory_continuity, memory_summary
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
            context = data["prior_context"] or {
                "overview": "Continue the test work.",
                **{k: [] for k in memory_summary.FIELDS},
            }
            if refs:
                context["constraints"] = [
                    {"text": "Retain the current owner constraints.", "refs": refs}
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
        assert first["memory_sources"] and first["retained_sources"]
        again = await server.progress_coordinator.refresh("agent")
        assert again["id"] == first["id"] and len(calls) == 2
        append(path, "New owner constraint")
        second = await server.progress_coordinator.refresh("agent")
        assert second["id"] != first["id"]
        data = json.loads(calls[2].split("MEMORY UPDATE:\n", 1)[1])
        assert [r["text"] for r in data["records"] if r["update_kind"] != "work_record"] == [
            "New owner constraint"
        ]
        assert any(r["update_kind"] == "work_record" for r in data["records"])
        assert data["prior_context"]
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert cp["record"]["summary_ref"] == second["id"]
        assert len(calls) == 4
        assert server.digests.revision("agent", first["id"])["continuity"] == first["continuity"]

    asyncio.run(run())


def test_backlog_is_one_bounded_update_and_failure_never_advances_coverage(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Older original")
    for n in range(100):
        append(path, f"Fact {n}: " + "x" * 1000)
    calls = provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        assert len(calls) == 2
        assert (
            0 < first["continuity"]["summarized_records"] < first["continuity"]["available_records"]
        )
        assert first["summary_validation"] == {"ready": True, "reason_codes": []}
        data = json.loads(calls[0].split("MEMORY UPDATE:\n", 1)[1])
        assert len(json.dumps(data["records"]).encode()) < memory_continuity.MAX_UPDATE_BYTES
        second = await server.progress_coordinator.refresh("agent")
        assert (
            second["continuity"]["summarized_records"] > first["continuity"]["summarized_records"]
        )
        cached = server.history.session("agent")["progress"]
        monkeypatch.setattr(
            "duckterm.server.summarize", lambda _: SimpleNamespace(text="quota exhausted")
        )
        assert await server.progress_coordinator.refresh("agent") is None
        assert server.history.session("agent")["progress"] == cached

    asyncio.run(run())


def test_rewritten_record_invalidates_prior_context_instead_of_reciting_removed_claim(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Prior owner direction")
    calls = provider(monkeypatch)

    async def run():
        await server.progress_coordinator.refresh("agent")
        transcript(tmp, "agent-claude", "Corrected direction")
        revision = await server.progress_coordinator.refresh("agent")
        data = json.loads(calls[2].split("MEMORY UPDATE:\n", 1)[1])
        assert data["prior_context"] is None
        assert revision["continuity"]["prior_coverage_invalidated"]

    asyncio.run(run())


def test_automatic_cadence_persists_attempt_even_when_provider_failed(memory_rig, monkeypatch):
    server, _, _, _ = memory_rig
    calls = []

    async def failed(key):
        calls.append(key)

    monkeypatch.setattr(server, "_refresh_progress", failed)
    now = [2_000_000.0]
    monkeypatch.setattr("duckterm.server.time.time", lambda: now[0])

    async def run():
        server._maybe_refresh_progress("agent")
        await asyncio.sleep(0)
        assert calls == ["agent"]
        server._progress_marks.clear()  # simulate server restart
        server.bus.publish({"event_type": "UserPromptSubmit", "session_key": "agent", "test": True})
        now[0] += 899
        server._maybe_refresh_progress("agent")
        server._progress_on_exit("agent")
        await asyncio.sleep(0)
        assert calls == ["agent"]
        now[0] += 1
        server._maybe_refresh_progress("agent")
        await asyncio.sleep(0)
        assert calls == ["agent", "agent"]

    asyncio.run(run())


def test_manual_checkpoint_joins_running_automatic_summary(memory_rig, monkeypatch):
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
            assert len(server.progress_coordinator.pending) == 1
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


def test_backfill_is_labeled_older_than_prior_summary_and_new_append(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Earlier direction: " + "x" * 30000)
    append(path, "Later correction: " + "y" * 30000)
    calls = provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        assert first["continuity"]["remaining_records"] == 1
        append(path, "New owner message after the first summary")
        await server.progress_coordinator.refresh("agent")
        data = json.loads(calls[2].split("MEMORY UPDATE:\n", 1)[1])
        older = next(r for r in data["records"] if r["text"].startswith("Earlier direction"))
        new = next(r for r in data["records"] if r["text"].startswith("New owner message"))
        assert older["update_kind"] == "historical_backfill" and older["position"] == 0
        assert new["update_kind"] == "new_since_previous_update" and new["position"] == 2
        assert not any(r["text"].startswith("Later correction") for r in data["records"])
        assert data["prior_context"] == first["continuity"]["context"]
        assert "NOT conversation chronology" in calls[2]

    asyncio.run(run())


def test_bounded_checkpoint_reports_updated_without_claiming_complete_history(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Original constraint")
    for n in range(100):
        append(path, f"Fact {n}: " + "x" * 1000)
    calls = provider(monkeypatch)

    async def run():
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert len(calls) == 2  # One bounded update, not an unbounded backlog loop.
        assert cp["summary_update"]["state"] == "updated"
        assert cp["summary_update"]["reason"] is None
        revision = server.digests.revision("agent", cp["summary_update"]["revision_id"])
        assert revision["id"] == cp["record"]["summary_ref"]
        assert revision["continuity"]["remaining_records"] > 0
        assert revision["continuity"]["verified"]
        assert revision["continuity"]["update_complete"]
        assert cp["handoff_eligible"]  # Still rechecked against current work before any switch.

    asyncio.run(run())


def test_manual_checkpoint_shares_automatic_failure_without_a_second_attempt(
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
            if task is coordinator.active.get("agent") and asyncio.current_task() is manual:
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


def test_oversized_current_work_preserves_revision_without_provider_call(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep the current owner constraint")
    calls = provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        cached = server.history.session("agent")["progress"]
        server.history.set_meta("agent", notes="🔒" * 6500)
        outcome = await server.progress_coordinator.refresh_result("agent")
        assert outcome.state == "failed" and outcome.reason == "invalid_context"
        assert len(calls) == 2
        assert server.history.session("agent")["progress"] == cached
        assert server.digests.revision("agent", first["id"])["continuity"] == first["continuity"]

    asyncio.run(run())
