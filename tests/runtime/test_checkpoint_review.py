"""Review failures remain blocking and retain bounded, distinct diagnostics."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_maintained_progress import append, provider
from test_memory import memory_rig, transcript

from duckterm.core import progress
from duckterm.core.saved_progress import SummaryUpdate

__all__ = ["memory_rig"]


@pytest.mark.parametrize(
    "validation,state,codes",
    [
        (None, "missing_verdict", []),
        ({}, "invalid_verdict", []),
        ({"ready": "true", "reason_codes": []}, "invalid_verdict", []),
        ({"ready": True, "reason_codes": []}, "accepted", []),
        (
            {"ready": False, "reason_codes": ["required_context_missing"]},
            "rejected",
            ["required_context_missing"],
        ),
        ({"ready": False, "reason_codes": []}, "rejected", []),
        (
            {"ready": True, "reason_codes": ["contradiction"]},
            "inconsistent_verdict",
            ["contradiction"],
        ),
        ({"ready": False, "reason_codes": "private reviewer prose"}, "invalid_reason_codes", []),
        (
            {"ready": False, "reason_codes": ["private prose with spaces"]},
            "invalid_reason_codes",
            [],
        ),
        ({"ready": False, "reason_codes": ["a"] * 17}, "invalid_reason_codes", []),
    ],
)
def test_review_states_are_distinct_and_do_not_retain_arbitrary_prose(validation, state, codes):
    assert progress.summary_review(validation) == {"state": state, "reason_codes": codes}


def test_missing_review_is_not_fabricated_as_reviewer_rejection():
    parsed = progress.parse_verdicts('{"accept":[],"done_next_action_ids":[]}')
    assert parsed["summary_validation"] is None
    assert progress.parse_verdicts('{"accept":[],"done_next_action_ids":null}') is None
    result = SummaryUpdate.from_revision({"id": "test", "summary": "Stored"})
    assert result.state == "failed" and result.diagnostic == "review_missing_verdict"


def test_inconsistent_stored_verdict_cannot_be_reported_as_updated():
    result = SummaryUpdate.from_revision(
        {"id": "test", "summary_validation": {"ready": True, "reason_codes": ["contradiction"]}}
    )
    assert result.state == "failed"
    assert result.diagnostic == "review_inconsistent_verdict:contradiction"


@pytest.mark.parametrize(
    "reply,state,codes",
    [
        ("private malformed reviewer response", "malformed_reply", []),
        ({"accept": []}, "missing_verdict", []),
        (
            {
                "accept": [],
                "summary_validation": {
                    "ready": False,
                    "reason_codes": ["required_context_missing"],
                },
            },
            "rejected",
            ["required_context_missing"],
        ),
        (
            {
                "accept": [],
                "summary_validation": {"ready": True, "reason_codes": ["contradiction"]},
            },
            "inconsistent_verdict",
            ["contradiction"],
        ),
        ({"accept": [], "summary_validation": {"ready": True}}, "invalid_reason_codes", []),
    ],
)
def test_checkpoint_attempt_keeps_review_diagnostic_and_last_good_revision(
    memory_rig, monkeypatch, reply, state, codes
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Initial owner constraint")
    provider(monkeypatch)
    import duckterm.server as server_module

    generate = server_module.summarize

    async def checkpoint():
        return await server._create_checkpoint("agent", server.history.session("agent"), "manual")

    async def run():
        first = await checkpoint()
        assert first["summary_update"]["state"] == "updated"
        append(path, "Keep an additional owner constraint")

        def rejected(prompt, **kwargs):
            if prompt.startswith("You are validating"):
                return SimpleNamespace(text=reply if isinstance(reply, str) else json.dumps(reply))
            return generate(prompt, **kwargs)

        monkeypatch.setattr(server_module, "summarize", rejected)
        failed = await checkpoint()
        attempt = failed["summary_update"]
        assert attempt["state"] == "failed" and attempt["revision_id"] is None
        assert attempt["reason"] == "summary_unverified"
        assert attempt["review"] == {"state": state, "reason_codes": codes}
        assert attempt["diagnostic"].startswith("review_" + state)
        assert failed["record"]["summary_ref"] == first["record"]["summary_ref"]
        assert failed["summary"] == first["summary"]
        stored = server.history._conn.execute(
            "SELECT record_json FROM checkpoints WHERE id=?", (failed["id"],)
        ).fetchone()[0]
        assert json.loads(stored)["summary_update"] == attempt
        assert "private malformed reviewer response" not in stored

    asyncio.run(run())


def test_malformed_envelope_keeps_valid_rejection_codes_without_accepting_summary():
    text = json.dumps({"summary_validation": {"ready": False, "reason_codes": ["contradiction"]}})
    verdicts = progress.parse_verdicts(text)
    assert verdicts is None
    assert progress.review_outcome(text, verdicts) == {
        "state": "malformed_reply",
        "reason_codes": ["contradiction"],
    }


def test_accepted_review_with_failed_source_fence_is_not_called_a_reviewer_rejection():
    result = SummaryUpdate.from_revision(
        {
            "id": "test",
            "summary_review": {"state": "accepted", "reason_codes": []},
            "summary_validation": {"ready": False, "reason_codes": ["legacy_baseline_unreviewed"]},
        }
    )
    assert result.state == "failed"
    assert result.diagnostic == "summary_not_ready:legacy_baseline_unreviewed"
    assert result.review["state"] == "accepted"
