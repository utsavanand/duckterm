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


@pytest.mark.parametrize("changed", ["transcript", "policy"])
def test_second_prompt_probe_is_fenced_without_another_await(rig, monkeypatch, changed):
    import os
    from pathlib import Path

    from duckterm.core.saved_progress import transcript_fence

    server, _, _, calls = rig
    fence = transcript_fence(server._message_source("a"))
    assert fence and fence["version"]
    path = Path(fence["path"])
    original = server.restarts.draft_free
    validations = 0
    mutated = False
    original_validate = harness_switch.validate

    async def validate(*args):
        nonlocal validations
        await original_validate(*args)
        validations += 1

    monkeypatch.setattr(harness_switch, "validate", validate)

    async def draft_free(*args):
        nonlocal mutated
        result = await original(*args)
        if validations == 2 and not mutated:
            mutated = True
            if changed == "policy":
                monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "changed-at-final-probe")
            else:
                stat = path.stat()
                raw = path.read_bytes()
                # Same bytes/length/mtime can still represent a replaced or
                # concurrently rewritten source. ctime must fence that write.
                path.write_bytes(raw)
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return result

    monkeypatch.setattr(server.restarts, "draft_free", draft_free)

    async def run():
        await server.restarts.request("a", "", "claude-code", interrupt=True)
        await drain(server)
        assert mutated and validations == 2
        assert not calls
        assert server.restarts.read("a")["status"] == "failed"

    asyncio.run(run())


def test_draft_changed_during_added_source_validation_is_preserved(rig, monkeypatch):
    server, _, screen, calls = rig
    original = harness_switch.validate
    validations = 0

    async def validate(*args):
        nonlocal validations
        await original(*args)
        validations += 1
        if validations == 2:
            screen[0] = "────────────────\n› New owner draft\n────────────────"

    monkeypatch.setattr(harness_switch, "validate", validate)

    async def run():
        await server.restarts.request("a", "", "claude-code", interrupt=True)
        await drain(server)
        assert validations == 2
        assert not calls
        assert "New owner draft" in screen[0]
        assert server.restarts.read("a")["status"] == "failed"

    asyncio.run(run())
