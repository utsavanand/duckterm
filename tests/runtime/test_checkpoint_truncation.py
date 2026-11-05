"""A checkpoint saves a complete candidate, never a sliced prefix."""

import asyncio
import json

from test_maintained_progress import append
from test_maintained_repair import candidate
from test_memory import memory_rig, transcript

from duckterm.llm.summarizer import Summary

__all__ = ["memory_rig"]

LONG = (
    "The implementation is staged and has passed local tests. "
    + "The owner must approve deployment before anything is released. " * 105
    + "Until that approval arrives, keep production unchanged."
)
SHORT = (
    "The staged implementation passed local tests. "
    "Keep production unchanged until the owner approves deployment."
)


def test_checkpoint_rewrites_overlong_summary_and_saves_it_intact(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", SHORT)
    calls = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        assert not prompt.startswith("You are validating")
        value = candidate(prompt)
        value["summary"] = LONG if len(calls) == 1 else SHORT
        if len(calls) == 2:
            assert "summary_too_long" in prompt
            assert "within 6000 characters" in prompt
        return Summary(json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)

    async def run():
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert cp["summary_update"]["state"] == "updated"
        revision = server.digests.revision("agent", cp["record"]["summary_ref"])
        assert cp["summary"] == revision["summary"] == SHORT
        assert len(calls) == 2  # one bad draft, one rewrite
        assert revision["summary_review"]["state"] == "not_reviewed"
        again = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert again["record"]["summary_ref"] == revision["id"] and len(calls) == 2

    asyncio.run(run())


def test_repeated_overlong_drafts_fail_explicitly_and_keep_last_good_checkpoint(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", SHORT)
    overlong = [False]
    calls = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        assert not prompt.startswith("You are validating")
        value = candidate(prompt)
        value["summary"] = LONG if overlong[0] else SHORT
        return Summary(json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)

    async def run():
        first = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        prior = server.digests.revision("agent", first["record"]["summary_ref"])
        cache = server.history.session("agent")["progress"]
        append(path, "There is new work to summarize.")
        overlong[0] = True
        failed = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert failed["summary_update"]["state"] == "failed"
        assert failed["summary_update"]["reason"] == "invalid_summary"
        assert failed["summary_update"]["diagnostic"] == "summary_too_long"
        assert failed["summary_update"]["revision_id"] is None
        assert failed["record"]["summary_ref"] == prior["id"]
        assert failed["summary"] == SHORT
        assert server.digests.revision("agent", prior["id"]) == prior
        assert server.history.session("agent")["progress"] == cache
        assert len(calls) == 3  # successful first attempt plus exactly two bad drafts
        assert LONG not in json.dumps(failed)

    asyncio.run(run())
