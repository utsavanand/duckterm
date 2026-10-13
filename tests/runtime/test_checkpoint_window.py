"""Manual checkpoints bound the whole lookback and retain immutable evidence."""

import asyncio
import json

import pytest
from test_maintained_progress import append
from test_memory import memory_rig, transcript
from test_memory_spans import provider

from duckterm import memory_checkpoint, memory_sources
from duckterm.persistence.saved_state import current_revision

__all__ = ["memory_rig"]

OPTIONS = {
    "lookback_records": 20,
    "batch_records": 20,
    "writer_model": "haiku",
    "reviewer_model": "sonnet",
}


def model_provider(monkeypatch, change=None):
    from duckterm import server as server_module

    calls, _ = provider(monkeypatch)
    generate = server_module.summarize
    models = []

    def run(prompt, **kwargs):
        models.append(kwargs.get("claude_model"))
        answer = generate(prompt)
        if change:
            change(prompt)
        return answer

    monkeypatch.setattr(server_module, "summarize", run)
    return calls, models


async def checkpoint(server, lookback=20):
    coordinator = server.progress_coordinator
    captured = await coordinator.capture("agent")
    captured = await memory_checkpoint.freeze(
        server, "agent", captured, {**OPTIONS, "lookback_records": lookback}
    )
    return await coordinator.refresh_result("agent", captured)


def writer_texts(calls):
    return [
        [r["text"] for r in json.loads(prompt.split("MEMORY UPDATE:\n", 1)[1])["records"]]
        for prompt in calls
        if not prompt.startswith("You are validating")
    ]


def test_default_twenty_excludes_backlog_and_model_roles_are_explicit(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Record 0")
    for n in range(1, 61):
        append(path, f"Record {n}")
    calls, models = model_provider(monkeypatch)

    async def run():
        result = await checkpoint(server)
        assert result.state == "updated", result
        assert writer_texts(calls) == [[f"Record {n}" for n in range(41, 61)]]
        assert models == ["haiku", "sonnet"]
        coverage = result.span_coverage
        assert coverage["scope"] == "recent_window"
        assert coverage["summarized_records"] == 20
        assert coverage["outside_window_records"] == 41
        assert coverage["pending_records"] == 0
        before = len(calls)
        repeated = await checkpoint(server)
        assert repeated.state == "reused"
        assert len(calls) == before

    asyncio.run(run())


def test_larger_window_batches_twenty_and_fills_holes_without_repeating(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Record 0")
    for n in range(1, 61):
        append(path, f"Record {n}")
    calls, _ = model_provider(monkeypatch)

    async def run():
        first = await checkpoint(server)
        assert first.state == "updated", first
        second = await checkpoint(server, 60)
        assert second.state == "updated", second
        batches = writer_texts(calls)
        assert [len(b) for b in batches] == [20, 20, 20]
        assert set(sum(batches, [])) == {f"Record {n}" for n in range(1, 61)}
        assert second.span_coverage["outside_window_records"] == 1
        # A later full-range preparation must fill the older hole, not pretend
        # the recent-window boundary covered it or refuse overlapping ranges.
        full = await server.progress_coordinator.refresh_result("agent")
        assert full.state == "updated", full
        assert writer_texts(calls)[-1] == ["Record 0"]

    asyncio.run(run())


def test_append_and_card_updates_during_generation_do_not_invalidate_snapshot(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Original captured instruction")
    changed = False
    loop = None

    def change(prompt):
        nonlocal changed
        if not changed:
            changed = True
            append(path, "Later instruction belongs to next checkpoint")
            loop.call_soon_threadsafe(
                lambda: server.history.set_meta("agent", notes="A newer note")
            )

    calls, _ = model_provider(monkeypatch, change)

    async def run():
        nonlocal loop
        loop = asyncio.get_running_loop()
        first = await checkpoint(server)
        assert first.state == "updated", first
        assert writer_texts(calls) == [["Original captured instruction"]]
        source = first.revision["memory_sources"][0]
        retained = memory_sources.read_native("agent", source)
        assert len(retained["records"]) == 1
        second = await checkpoint(server)
        assert second.state == "updated", second
        assert writer_texts(calls)[-1] == ["Later instruction belongs to next checkpoint"]

    asyncio.run(run())


@pytest.mark.parametrize("change", ["scope", "conversation"])
def test_identity_or_scope_change_still_refuses(memory_rig, monkeypatch, change):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Original captured instruction")

    loop = None

    def apply_change():
        if change == "scope":
            server.history.set_meta("agent", group="Elsewhere")
        else:
            server.history.set_harness_identity(
                "agent",
                "codex",
                None,
                None,
                control={
                    "native_binding": {
                        "runtime": "codex",
                        "native_id": "replacement",
                        "generation": "new",
                    }
                },
            )

    def mutate(prompt):
        loop.call_soon_threadsafe(apply_change)

    model_provider(monkeypatch, mutate)

    async def run():
        nonlocal loop
        loop = asyncio.get_running_loop()
        result = await checkpoint(server)
        assert result.state == "failed" and result.reason == "source_changed", result
        assert current_revision(server.history.session("agent")) is None

    asyncio.run(run())
