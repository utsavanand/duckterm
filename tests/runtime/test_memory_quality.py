"""Memory content quality: noise projection, semantic correction and longer narratives."""

import asyncio
import json

import pytest
from test_maintained_repair import candidate, review
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


def test_routine_setup_repair_keeps_feedback_and_reviews_both_fields(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(
        tmp,
        "agent-claude",
        "Keep the owner constraint. The inbox is empty. Research the business before a decision.",
    )
    calls = []
    correction = "Remove the routine setup paragraph and overview; keep the business research."

    def generate(prompt, **kwargs):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            if len(calls) == 2:
                return Summary(
                    json.dumps(
                        {
                            "accept": [],
                            "summary_corrections": [correction],
                            "summary_validation": {
                                "ready": False,
                                "reason_codes": ["routine_notice_noise"],
                            },
                        }
                    ),
                    "fixture",
                )
            summary = json.loads(prompt.split("SUMMARY:\n", 1)[1].split("\n\nSOURCE:", 1)[0])
            assert summary["summary"] == "Research the business before deciding."
            assert summary["context"]["overview"] == summary["summary"]
            return review()
        value = candidate(prompt)
        if len(calls) == 1:
            # A legal Unicode summary expands beyond the draft-copy budget.
            value["summary"] = "Setup (routine): " + "é" * 4000
            value["context"]["overview"] = "Setup (routine): inbox empty."
        else:
            assert correction in prompt
            assert "Keep the owner constraint." in prompt
            value["summary"] = "Research the business before deciding."
            value["context"]["overview"] = "Research the business before deciding."
        return Summary(json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    assert cp["summary_update"]["state"] == "updated", cp
    assert len(calls) == 4
    saved = server.digests.revision("agent", cp["record"]["summary_ref"])
    assert saved["summary"] == "Research the business before deciding."
    assert saved["continuity"]["context"]["overview"] == saved["summary"]


@pytest.mark.parametrize("issue", ["semantic", "format"])
def test_full_span_leaves_room_for_correction_and_preserves_pending_records(
    memory_rig, monkeypatch, issue
):
    from test_maintained_progress import append

    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Keep the owner constraint. " + "x" * 500)
    for number in range(30):
        append(path, f"Evidence {number}: " + "y" * 500)
    calls = []
    correction = (
        "Remove routine setup from the summary and overview; preserve the owner constraint. "
        + "é" * 300
    )

    def generate(prompt, **kwargs):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            if issue == "semantic" and len(calls) == 2:
                return Summary(
                    json.dumps(
                        {
                            "accept": [],
                            "summary_corrections": [correction],
                            "summary_validation": {
                                "ready": False,
                                "reason_codes": ["routine_notice_noise"],
                            },
                        }
                    ),
                    "fixture",
                )
            assert "Research completed; keep the owner constraint." in prompt
            return review()
        assert len(prompt.encode()) <= (
            memory_spans.MAX_GENERATION_BYTES
            if len(calls) == 1
            else memory_spans.repair_input_limit()
        )
        value = candidate(prompt)
        if len(calls) == 1:
            value["summary"] = "Setup (routine): " + "é" * (4000 if issue == "semantic" else 6000)
            value["context"]["overview"] = "Setup: inbox empty."
        else:
            original, _ = json.JSONDecoder().raw_decode(calls[0].split("MEMORY UPDATE:\n", 1)[1])
            repaired, _ = json.JSONDecoder().raw_decode(prompt.split("MEMORY UPDATE:\n", 1)[1])
            assert repaired == original
            assert "Keep the owner constraint." in prompt
            if issue == "semantic":
                feedback, _ = json.JSONDecoder().raw_decode(
                    prompt.split("CORRECTION REQUIRED:\n", 1)[1]
                )
                assert feedback["review_feedback"] == [correction]
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
        assert len(calls) == (4 if issue == "semantic" else 3)
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


def test_review_feedback_that_cannot_fit_refuses_instead_of_retrying_blind(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep the owner constraint.")
    calls = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            return Summary(
                json.dumps(
                    {
                        "accept": [],
                        "summary_corrections": [
                            f"Remove routine setup detail {number}: " + "x" * 450
                            for number in range(8)
                        ],
                        "summary_validation": {
                            "ready": False,
                            "reason_codes": ["routine_notice_noise"],
                        },
                    }
                ),
                "fixture",
            )
        monkeypatch.setattr(memory_spans, "MAX_GENERATION_BYTES", len(prompt.encode()) + 2000)
        monkeypatch.setattr(memory_spans, "REPAIR_HEADROOM_BYTES", 0)
        return Summary(json.dumps(candidate(prompt)), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    assert cp["summary_update"]["state"] == "failed"
    assert cp["summary_update"]["diagnostic"] == "repair_input_too_large"
    assert cp["record"]["summary_ref"] is None
    assert server.history.session("agent")["progress"] is None
    assert len(calls) == 2


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


def test_stale_claim_is_repaired_and_reviewed_before_checkpoint_publication(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(
        tmp,
        "agent-claude",
        "Keep the owner constraint. The cause was unknown; later we diagnosed the comparison bug.",
    )
    calls = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            if len(calls) == 2:
                return Summary(
                    json.dumps(
                        {
                            "accept": [],
                            "done_next_action_ids": [],
                            "summary_corrections": [
                                {
                                    "field": "context.questions",
                                    "correction": "The later record diagnoses the comparison bug.",
                                }
                            ],
                            "summary_validation": {
                                "ready": False,
                                "reason_codes": ["stale_open_item"],
                            },
                        }
                    ),
                    "fixture",
                )
            assert "The comparison bug was diagnosed" in prompt
            return review()
        value = candidate(prompt)
        value["summary"] = (
            "The cause is unknown." if len(calls) == 1 else "The comparison bug was diagnosed."
        )
        if len(calls) > 1:
            assert "The later record diagnoses the comparison bug." in prompt
            assert "stale_open_item" in prompt
            assert "The cause was unknown; later we diagnosed" in prompt
        return Summary(json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    assert cp["summary_update"]["state"] == "updated", cp
    assert len(calls) == 4
    saved = server.digests.revision("agent", cp["record"]["summary_ref"])
    assert saved["summary"] == "The comparison bug was diagnosed."


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


@pytest.mark.parametrize("second_failure", ["rejected", "provider_timeout"])
def test_semantic_correction_is_bounded_and_never_saves_rejected_memory(
    memory_rig, monkeypatch, second_failure
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "The cause was diagnosed; keep the owner constraint.")
    calls = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        if len(calls) == 3 and second_failure == "provider_timeout":
            return Summary("", "none", "provider_timeout")
        if prompt.startswith("You are validating"):
            return Summary(
                json.dumps(
                    {
                        "accept": [],
                        "summary_validation": {"ready": False, "reason_codes": ["stale_open_item"]},
                    }
                ),
                "fixture",
            )
        return Summary(json.dumps(candidate(prompt)), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    assert len(calls) == (3 if second_failure == "provider_timeout" else 4)
    assert cp["summary_update"]["state"] == "failed"
    assert cp["record"]["summary_ref"] is None
    assert server.history.session("agent")["progress"] is None


@pytest.mark.parametrize("issue", ["format", "semantic"])
@pytest.mark.parametrize("room_for_correction", [True, False])
def test_correction_respects_generation_reserve_without_slicing_sources(
    memory_rig, monkeypatch, issue, room_for_correction
):
    server, _, _, tmp = memory_rig
    evidence = "Keep the owner constraint. " + "x" * 6000
    transcript(tmp, "agent-claude", evidence)
    calls = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            if issue == "semantic" and len(calls) == 2:
                return Summary(
                    json.dumps(
                        {
                            "accept": [],
                            "summary_corrections": [
                                "The later evidence resolves the earlier unknown."
                            ],
                            "summary_validation": {
                                "ready": False,
                                "reason_codes": ["stale_open_item"],
                            },
                        }
                    ),
                    "fixture",
                )
            return review()
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
            value["summary"] = "S" * (6001 if issue == "format" else 5800)
        else:
            if issue == "semantic":
                assert "The later evidence resolves the earlier unknown." in prompt
                assert "S" * 5800 not in prompt
            else:
                assert '"rejected_draft": ""' in prompt
        return Summary(json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    assert cp["summary_update"]["state"] == ("updated" if room_for_correction else "failed"), cp
    if not room_for_correction:
        assert cp["record"]["summary_ref"] is None
        assert len(calls) == (1 if issue == "format" else 2)
