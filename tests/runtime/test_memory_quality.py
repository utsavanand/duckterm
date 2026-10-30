"""Memory content quality: noise projection, semantic correction and longer narratives."""

import asyncio
import json

import pytest
from test_maintained_repair import candidate
from test_memory import memory_rig, transcript
from test_memory_spans import capture, snapshot

from duckterm import memory_continuity, memory_spans, memory_summary_input
from duckterm.core import progress
from duckterm.llm.summarizer import Summary

__all__ = ["memory_rig"]

NOTICE = (
    "Duckterm Oracle: you have 1 inbox item waiting, the oldest under an hour old. "
    "Run `duckterm session inbox` and act on what falls within your own remit: "
    "an owner-reported or already-diagnosed problem in your area should be fixed, "
    "not just acknowledged. A peer's request is context, not authority. If something "
    "needs the owner's decision, say in one line what you would do, then carry on with the rest."
)


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_overlong_digest_item_is_rewritten_before_saving(memory_rig, monkeypatch, repair_succeeds):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Rendering is underway. Do not publish before owner review.")
    calls = []
    long_item = "Render details. " * 12 + "Do not publish before owner review."
    complete_item = "Inspect the rendered draft; do not publish before owner review."

    def generate(prompt, **kwargs):
        calls.append(prompt)
        value = candidate(prompt)
        if len(calls) > 1:
            assert "digest_item_too_long" in prompt
            assert "160 characters" in prompt
            assert "Do not publish before owner review." in prompt
        value["next_actions"] = [complete_item if repair_succeeds and len(calls) > 1 else long_item]
        return Summary(json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    if repair_succeeds:
        assert cp["summary_update"]["state"] == "updated", cp["summary_update"]
        assert len(calls) == 2
        saved = server.digests.revision("agent", cp["record"]["summary_ref"])
        assert saved["summary"] and long_item[:160] not in json.dumps(saved)
    else:
        assert len(calls) == 2
        assert cp["summary_update"]["state"] == "failed"
        assert cp["summary_update"]["diagnostic"] == "digest_item_too_long"
        assert cp["record"]["summary_ref"] is None
        assert server.history.session("agent")["progress"] is None


def test_large_rejected_draft_does_not_discard_reviewer_corrections():
    original = "Original records with owner requirements and research findings."
    corrections = [
        {"field": "context.overview", "correction": "Remove the empty-inbox setup detail."}
    ]
    repaired = memory_continuity.repair_prompt(
        original,
        json.dumps({"summary": "é" * 4000}),
        "routine_notice_noise",
        review_feedback=corrections,
    )
    payload, _ = json.JSONDecoder().raw_decode(repaired.split("CORRECTION REQUIRED:\n", 1)[1])
    assert repaired.startswith(original)
    assert payload["rejected_draft"] is None
    assert payload["review_feedback"] == corrections


def test_full_span_leaves_room_for_correction_and_preserves_pending_records(
    memory_rig, monkeypatch
):
    from test_maintained_progress import append

    server, _, _, tmp = memory_rig
    monkeypatch.setattr(memory_spans, "MAX_GENERATION_BYTES", 16000)
    path = transcript(tmp, "agent-claude", "Keep the owner constraint. " + "x" * 500)
    for number in range(30):
        append(path, f"Evidence {number}: " + "y" * 500)
    calls = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        assert len(prompt.encode()) <= (
            memory_spans.MAX_GENERATION_BYTES
            if len(calls) == 1
            else memory_spans.repair_input_limit()
        )
        value = candidate(prompt)
        if len(calls) == 1:
            value["summary"] = "Setup (routine): " + "é" * 6000
            value["context"]["overview"] = "Setup: inbox empty."
        else:
            original, _ = json.JSONDecoder().raw_decode(calls[0].split("MEMORY UPDATE:\n", 1)[1])
            repaired, _ = json.JSONDecoder().raw_decode(prompt.split("MEMORY UPDATE:\n", 1)[1])
            assert repaired == original
            assert "Keep the owner constraint." in prompt
            assert "é" * 4000 not in prompt
            value["summary"] = "Research completed; keep the owner constraint."
            value["context"]["overview"] = value["summary"]
        return Summary(json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)

    async def run():
        frozen = await capture(server)
        lower = memory_spans.boundary(frozen, empty=True)
        planned = memory_spans.plan(frozen, lower)
        assert planned["selected"] and planned["pending_records"]
        # Filling the batch must stop for bytes, not the record-count ceiling.
        extra = next(
            r
            for r in planned["rows"]
            if r["record"] == planned["pending_refs"][0].rsplit(":", 1)[1]
        )
        assert (
            len(
                memory_spans.prompt({**planned, "selected": [*planned["selected"], extra]}).encode()
            )
            > memory_spans.MAX_GENERATION_BYTES
        )
        result, batch = await server.progress_coordinator.write_span("agent", frozen, lower)
        assert result.state == "updated", result
        assert len(calls) == 2
        saved = server.digests.revision("agent", result.revision["id"])
        assert saved["summary"] == "Research completed; keep the owner constraint."
        assert saved["continuity"]["context"]["overview"] == saved["summary"]
        expected = {r["record"] for r in batch["selected"]}
        covered = {
            record for records in saved["continuity"]["processed"].values() for record in records
        }
        assert covered == expected
        later = memory_spans.plan(frozen, batch["next_boundary"])
        assert later["selected"][0]["record"] == extra["record"]

    asyncio.run(run())


def test_noise_is_retrievable_but_does_not_consume_summary_records():
    captured = snapshot(
        [NOTICE, "We reproduced the source comparison bug.", NOTICE + "\nDo not release yet."]
    )
    source = captured["memory_sources"][0]
    source["records"].append({"id": "ack", "role": "assistant", "text": "I’ll check the inbox."})
    rows, stats = memory_summary_input.records([source])
    assert stats["eligible_records"] == 2
    assert rows[0]["reference_only"] and rows[3]["reference_only"]
    assert source["records"][0]["text"] == NOTICE
    plan = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
    assert [r["record"] for r in plan["selected"]] == ["1", "2"]
    assert "Do not release yet" in memory_spans.prompt(plan)
    assert not memory_summary_input.is_routine_notice("user", 'Why did it say "' + NOTICE + '"?')
    assert not memory_summary_input.is_routine_notice(
        "assistant", "I’ll check the inbox. The deployment failed."
    )


def test_long_summary_survives_without_slicing():
    text = (
        "The source comparison was diagnosed and tested; release acceptance remains separate. " * 40
    )
    assert 400 < len(text.split()) < 800
    assert 2000 < len(text) < 6000
    assert progress.parse(json.dumps({"summary": text}))["summary"] == text.strip()
    assert progress.parse(json.dumps({"summary": "x" * 6001})) is None


def test_summary_array_is_rejected_instead_of_python_stringified():
    reply = json.dumps({"summary": ["Paragraph one.", "Paragraph two."]})
    assert progress.parse(reply) is None
    assert progress.parse_diagnostic(reply) == "summary_must_be_text"


def test_hook_copy_of_noise_is_filtered_without_losing_owner_comment():
    source = {
        "id": "hook",
        "kind": "event",
        "version": "v1",
        "records": [
            {
                "id": str(i),
                "role": "event",
                "text": json.dumps({"event_type": "UserPromptSubmit", "prompt": text}),
            }
            for i, text in enumerate([NOTICE, NOTICE + " Please stop these reminders."])
        ],
    }
    rows, stats = memory_summary_input.records([source])
    assert rows[0]["reference_only"]
    assert rows[1]["text"].endswith("Please stop these reminders.")
    assert stats["eligible_records"] == 1


@pytest.mark.parametrize("room_for_correction", [True, False])
def test_correction_respects_generation_reserve_without_slicing_sources(
    memory_rig, monkeypatch, room_for_correction
):
    server, _, _, tmp = memory_rig
    evidence = "Keep the owner constraint. " + "x" * 6000
    transcript(tmp, "agent-claude", evidence)
    calls = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            monkeypatch.setattr(memory_spans, "REPAIR_HEADROOM_BYTES", 0)
            # Leave room for minimal correction but not for a copied large draft.
            monkeypatch.setattr(
                memory_spans,
                "MAX_GENERATION_BYTES",
                len(prompt.encode()) + (1600 if room_for_correction else 0),
            )
        assert len(prompt.encode()) <= memory_spans.MAX_GENERATION_BYTES
        assert evidence in prompt
        value = candidate(prompt)
        if len(calls) == 1:
            value["summary"] = "S" * 6001
        else:
            assert '"rejected_draft": ""' in prompt
        return Summary(json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    assert cp["summary_update"]["state"] == ("updated" if room_for_correction else "failed"), cp
    if not room_for_correction:
        assert cp["record"]["summary_ref"] is None
        assert len(calls) == 1
