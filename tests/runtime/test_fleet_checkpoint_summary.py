"""The fleet chat digest carries the checkpoint summary, labelled with its state."""

import asyncio

from test_maintained_progress import append, provider
from test_memory import memory_rig, transcript

from duckterm.core import fleet_questions
from duckterm.llm.summarizer import Summary

__all__ = ["memory_rig"]


def digest(server):
    return asyncio.run(
        fleet_questions.digest(server, server.history.session("agent"), "what is agent doing?")
    )


def test_ready_summary_replaces_the_checkpoint_label(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Build the export")
    provider(monkeypatch)
    cp = asyncio.run(
        server._create_checkpoint("agent", server.history.session("agent"), "Automatic")
    )
    assert cp["summary_state"] == "ready"
    text = digest(server)
    assert "checkpoint summary (ready, as of " in text
    assert (
        text.split("checkpoint summary (ready, as of ", 1)[1]
        .split("): ", 1)[1]
        .startswith("Continue the test work.")
    )
    assert "Automatic" not in text


def test_stale_summary_is_marked_not_verified(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Build the export")
    provider(monkeypatch)
    asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    append(path, "An additional requirement")
    monkeypatch.setattr("duckterm.server.summarize", lambda _, **_kw: Summary("", "none"))
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    assert cp["summary_state"] == "stale"
    assert "checkpoint summary (stale, not verified, as of " in digest(server)


def test_no_checkpoint_says_none_yet(memory_rig):
    server, _, _, _ = memory_rig
    assert "checkpoint summary: none yet" in digest(server)
