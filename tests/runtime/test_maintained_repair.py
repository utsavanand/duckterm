"""Rejected memory drafts get one correction before the existing review/write path."""

import asyncio
import json
import threading

import pytest
from test_memory import memory_rig, transcript

from duckterm import memory_continuity, memory_summary
from duckterm.llm.summarizer import Summary

__all__ = ["memory_rig"]


def candidate(prompt):
    data, _ = json.JSONDecoder().raw_decode(prompt.split("MEMORY UPDATE:\n", 1)[1])
    ref = next(r["ref"] for r in data["records"] if r["role"] == "user")
    return {
        "summary": "Keep the owner constraint and finish the work.",
        "context": {
            "overview": "Keep the owner constraint and finish the work.",
            **{name: [] for name in memory_summary.FIELDS},
            "constraints": [{"text": "Keep the owner constraint.", "refs": [ref]}],
        },
    }


def review(ready=True):
    return Summary(
        json.dumps(
            {
                "accept": [],
                "done_next_action_ids": [],
                "summary_validation": {
                    "ready": ready,
                    "reason_codes": [] if ready else ["missing_constraint"],
                },
            }
        ),
        "fixture",
    )


def rejected(value, issue):
    if issue == "invalid_digest":
        return "not JSON"
    if issue == "invalid_overview":
        value["context"]["overview"] = ["Wrong shape"]
    elif issue == "unknown_reference":
        value["context"]["constraints"][0]["refs"] = ["invented:reference"]
    elif issue == "invalid_references":
        value["context"]["constraints"][0]["refs"] = [{"invented": "reference"}]
    elif issue == "overview_too_large":
        value["context"]["overview"] = "é" * 401
    elif issue == "context_too_large":
        value["context"]["constraints"] = [
            {"text": "Keep required fact. " + "x" * 1500, "refs": item["refs"]}
            for item in value["context"]["constraints"] * 8
        ]
    return json.dumps(value)


@pytest.mark.parametrize(
    "issue",
    [
        "invalid_digest",
        "invalid_overview",
        "unknown_reference",
        "invalid_references",
        "overview_too_large",
        "context_too_large",
    ],
)
def test_rejected_draft_is_corrected_reviewed_and_checkpoint_reuses(memory_rig, monkeypatch, issue):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep the owner constraint.")
    calls = []

    def generate(prompt):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            assert "invented:reference" not in prompt
            return review()
        value = candidate(prompt)
        return Summary(rejected(value, issue) if len(calls) == 1 else json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)

    async def run():
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert cp["summary_update"]["state"] == "updated"
        revision = server.digests.revision("agent", cp["record"]["summary_ref"])
        assert revision["continuity"]["verified"]
        assert (
            revision["continuity"]["context"]["constraints"][0]["text"]
            == "Keep the owner constraint."
        )
        assert len(calls) == 3
        assert calls[1].startswith(calls[0]) and issue in calls[1]
        assert "CORRECTION REQUIRED" not in calls[2]
        again = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert again["record"]["summary_ref"] == revision["id"] and len(calls) == 3

    asyncio.run(run())


def test_repeated_invalid_output_stops_and_saves_only_safe_diagnostic(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep the owner constraint.")
    calls = []

    def generate(prompt):
        calls.append(prompt)
        value = candidate(prompt)
        value["context"]["constraints"][0]["refs"] = ["PRIVATE-INVALID-REFERENCE"]
        return Summary(json.dumps(value), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    assert len(calls) == 2
    assert cp["summary_update"]["state"] == "failed"
    assert cp["summary_update"]["reason"] == "invalid_context"
    assert cp["summary_update"]["diagnostic"] == "unknown_reference"
    assert cp["record"]["summary_ref"] is None
    assert server.history.session("agent")["progress"] is None
    assert "PRIVATE-INVALID-REFERENCE" not in json.dumps(cp)


@pytest.mark.parametrize(
    "failure", ["provider_timeout", "provider_failed", "disabled", "no_provider"]
)
def test_provider_failure_has_no_correction_call(memory_rig, monkeypatch, failure):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep the owner constraint.")
    calls = []

    def generate(prompt):
        calls.append(prompt)
        return Summary("", "none", failure)

    monkeypatch.setattr("duckterm.server.summarize", generate)
    outcome = asyncio.run(server.progress_coordinator.refresh_result("agent"))
    assert outcome.state == "failed" and outcome.reason == failure
    assert len(calls) == 1
    assert server.history.session("agent")["progress"] is None


@pytest.mark.parametrize("change", ["transcript", "task", "scope", "policy"])
def test_source_change_during_bad_draft_prevents_correction(memory_rig, monkeypatch, change):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Keep the owner constraint.")
    calls = []
    original = server.progress_coordinator.capture

    async def capture(key):
        if calls:
            if change == "transcript":
                path.write_text(path.read_text().replace("constraint", "CORRECTION"))
            elif change == "task":
                server.history.set_meta("agent", notes="Owner changed current work")
            elif change == "scope":
                server.history.set_meta("agent", group="Elsewhere")
            else:
                monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "changed-configuration")
        return await original(key)

    def generate(prompt):
        calls.append(prompt)
        return Summary("not JSON", "fixture")

    monkeypatch.setattr(server.progress_coordinator, "capture", capture)
    monkeypatch.setattr("duckterm.server.summarize", generate)
    outcome = asyncio.run(server.progress_coordinator.refresh_result("agent"))
    assert outcome.state == "failed" and outcome.reason == "source_changed"
    assert len(calls) == 1
    assert server.history.session("agent")["progress"] is None


def test_corrected_draft_still_requires_review_and_preserves_last_good(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Keep the owner constraint.")
    phase = ["initial"]
    calls = []

    def generate(prompt):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            return review(phase[0] == "initial")
        if phase[0] == "bad":
            phase[0] = "corrected"
            return Summary("not JSON", "fixture")
        return Summary(json.dumps(candidate(prompt)), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        cached = server.history.session("agent")["progress"]
        path.write_text(
            path.read_text()
            + json.dumps(
                {"type": "user", "message": {"role": "user", "content": "Additional owner work"}}
            )
            + "\n"
        )
        phase[0] = "bad"
        outcome = await server.progress_coordinator.refresh_result("agent")
        assert outcome.state == "failed" and outcome.reason == "summary_unverified"
        assert len(calls) == 5
        assert server.history.session("agent")["progress"] == cached
        assert server.digests.revision("agent", first["id"])["continuity"] == first["continuity"]

    asyncio.run(run())


@pytest.mark.parametrize("rejected_text", ["x" * 16001, "\x00" * 4000, "\ud800" * 4000])
def test_oversized_rejected_text_is_not_added_to_correction_evidence(rejected_text):
    prompt = "Original source including essential constraint."
    text = memory_continuity.repair_prompt(prompt, rejected_text, "invalid_digest")
    assert text.startswith(prompt) and '"rejected_draft": null' in text
    assert len(text.encode()) < 2000


def test_invalid_unicode_has_a_safe_failure_category():
    value = {"overview": "\ud800", **{k: [] for k in memory_summary.FIELDS}}
    with pytest.raises(memory_summary.InvalidContext) as exc:
        memory_summary.parse(json.dumps(value), set())
    assert exc.value.reason == "invalid_unicode"


def test_exhausted_correction_keeps_prior_revision_and_coverage(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Keep the owner constraint.")
    broken = [False]
    calls = []

    def generate(prompt):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            return review()
        value = candidate(prompt)
        return Summary(
            rejected(value, "unknown_reference") if broken[0] else json.dumps(value), "fixture"
        )

    monkeypatch.setattr("duckterm.server.summarize", generate)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        cached = server.history.session("agent")["progress"]
        path.write_text(
            path.read_text()
            + json.dumps(
                {"type": "user", "message": {"role": "user", "content": "Additional work"}}
            )
            + "\n"
        )
        broken[0] = True
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert cp["summary_update"]["state"] == "failed"
        assert cp["summary_update"]["diagnostic"] == "unknown_reference"
        assert cp["record"]["summary_ref"] == first["id"]
        assert len(calls) == 4
        assert server.history.session("agent")["progress"] == cached
        assert server.digests.revision("agent", first["id"])["continuity"] == first["continuity"]

    asyncio.run(run())


def test_coalesced_checkpoint_keeps_one_repair_when_other_waiter_cancels(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep the owner constraint.")
    started, release = threading.Event(), threading.Event()
    calls = []

    def generate(prompt):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            return review()
        if len(calls) == 1:
            return Summary("not JSON", "fixture")
        started.set()
        assert release.wait(5)
        return Summary(json.dumps(candidate(prompt)), "fixture")

    monkeypatch.setattr("duckterm.server.summarize", generate)

    async def run():
        coordinator = server.progress_coordinator
        automatic = asyncio.create_task(coordinator.refresh_result("agent"))
        joined = asyncio.Event()
        original = asyncio.shield

        def observed(task):
            if task is coordinator.active.get("agent") and asyncio.current_task() is manual:
                joined.set()
            return original(task)

        try:
            assert await asyncio.to_thread(started.wait, 5)
            monkeypatch.setattr(asyncio, "shield", observed)
            manual = asyncio.create_task(
                server._create_checkpoint("agent", server.history.session("agent"), "manual")
            )
            await asyncio.wait_for(joined.wait(), 5)
            automatic.cancel()
            with pytest.raises(asyncio.CancelledError):
                await automatic
            release.set()
            cp = await asyncio.wait_for(manual, 5)
            assert cp["summary_update"]["state"] == "updated"
            revision = server.digests.revision("agent", cp["record"]["summary_ref"])
            assert revision["continuity"]["verified"]
            assert len(calls) == 3
        finally:
            release.set()

    asyncio.run(run())
