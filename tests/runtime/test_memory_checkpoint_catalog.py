"""Checkpoint publication compares original evidence, not derived progress."""

import asyncio

from test_memory import memory_rig, transcript
from test_memory_spans import provider

from duckterm.persistence.saved_state import current_revision

__all__ = ["memory_rig"]


def test_existing_progress_does_not_make_unchanged_checkpoint_source_changed(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner constraint: retain exact source evidence.")
    server.digests.merge(
        "agent", [{"bucket": "learnings", "text": "An older derived progress note."}], [], 2
    )
    provider(monkeypatch)

    async def run():
        result = await server.progress_coordinator.refresh_result("agent")
        assert result.state == "updated", result
        assert current_revision(server.history.session("agent")) == result.revision["id"]

    asyncio.run(run())


def test_checkpoint_outcome_saves_success_with_existing_progress(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep evidence accessible after the checkpoint.")
    server.digests.merge(
        "agent", [{"bucket": "next_actions", "text": "Previously saved work."}], [], 2
    )
    provider(monkeypatch)

    async def run():
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        outcome = cp["record"]["summary_update"]
        assert outcome["state"] == "updated", outcome
        assert outcome["revision_id"] == current_revision(server.history.session("agent"))
        assert outcome["span_coverage"]["state"] == "complete"
        assert cp["record"]["summary_span_verified"] is True

    asyncio.run(run())


def test_live_edit_does_not_rewrite_the_captured_checkpoint(memory_rig, monkeypatch):
    from duckterm import server as server_module

    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Original owner constraint.")
    server.digests.merge("agent", [{"bucket": "learnings", "text": "Derived prior work."}], [], 2)
    provider(monkeypatch)
    generate = server_module.summarize

    def changed(prompt, **kwargs):
        result = generate(prompt, **kwargs)
        if prompt.startswith("You are validating"):
            path.write_text(
                path.read_text().replace("Original owner constraint.", "Replaced owner constraint.")
            )
        return result

    monkeypatch.setattr(server_module, "summarize", changed)

    async def run():
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        outcome = cp["record"]["summary_update"]
        assert outcome["state"] == "updated", outcome
        assert current_revision(server.history.session("agent")) == outcome["revision_id"]
        from duckterm.memory_sources import read_native

        saved = read_native("agent", cp["record"]["memory_sources"][0])
        assert saved["records"][0]["text"] == "Original owner constraint."

    asyncio.run(run())
