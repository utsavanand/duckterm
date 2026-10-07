"""A slow final prompt read must not hide changes after handoff validation."""

import asyncio

import pytest
from tests.runtime import test_restart_interrupt_switch as fixtures
from tests.runtime.test_restarts import drain

from duckterm import harness_switch

rig = fixtures.rig
checkpoint = fixtures.checkpoint


@pytest.mark.parametrize("changed", ["notes", "transcript", "policy"])
def test_change_during_final_prompt_probe_never_stops_source(rig, monkeypatch, changed):
    server, _, _, calls = rig
    text = [{"role": "assistant", "text": "Reviewed work"}]
    monkeypatch.setattr(server, "_progress_transcript", lambda *args: list(text))
    original_validate = harness_switch.validate
    original_draft_free = server.restarts.draft_free
    validated = False
    mutated = False

    async def validate(*args):
        nonlocal validated
        await original_validate(*args)
        validated = True

    async def draft_free(*args):
        nonlocal mutated
        result = await original_draft_free(*args)
        if validated and not mutated:
            mutated = True
            if changed == "notes":
                server.history.set_meta("a", notes="New owner constraint")
            elif changed == "transcript":
                text.append({"role": "assistant", "text": "New result while source is working"})
            else:
                monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "synthetic-new-policy")
        return result

    monkeypatch.setattr(harness_switch, "validate", validate)
    monkeypatch.setattr(server.restarts, "draft_free", draft_free)

    async def run():
        await server.restarts.request("a", "", "claude-code", interrupt=True)
        await drain(server)
        assert mutated
        assert not calls, "changed handoff must leave the source process intact"
        assert server.restarts.read("a")["status"] == "failed"

    asyncio.run(run())
