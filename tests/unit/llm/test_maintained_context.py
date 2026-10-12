"""Equivalent single citations normalize; unknown evidence remains rejected."""

import json

import pytest

from duckterm import memory_continuity, memory_summary
from duckterm.core.session_api import APIError


def fixture_plan():
    return {
        "selected": [{"source": "a" * 32, "version": "b" * 64, "record": "17"}],
        "prior_context": None,
    }


def response(reference):
    return json.dumps(
        {
            "context": {
                "overview": "Keep the owner constraint.",
                **{name: [] for name in memory_summary.FIELDS},
                "constraints": [{"text": "Keep the owner constraint.", "refs": reference}],
            }
        }
    )


def test_known_single_reference_normalizes_without_changing_claim_or_source():
    plan = fixture_plan()
    ref = "a" * 32 + ":" + "b" * 64 + ":17"
    value = memory_continuity.context(response(ref), plan)
    assert value["constraints"] == [{"text": "Keep the owner constraint.", "refs": [ref]}]
    assert value == memory_continuity.context(response([ref]), plan)


@pytest.mark.parametrize(
    "reference", ["unknown", "", [], ["unknown"], "first,second", {"ref": "unknown"}]
)
def test_malformed_or_unknown_citations_are_not_repaired(reference):
    with pytest.raises(APIError, match="invalid or oversized"):
        memory_continuity.context(response(reference), fixture_plan())


def test_normalized_reference_does_not_bypass_context_budget():
    ref = "a" * 32 + ":" + "b" * 64 + ":17"
    value = json.loads(response(ref))
    value["context"]["constraints"] *= 80
    with pytest.raises(APIError, match="invalid or oversized"):
        memory_continuity.context(json.dumps(value), fixture_plan())


def required_fixture(note_size=2000):
    task = {"id": "task-a", "note": "t" * note_size}
    mail = {"id": "q-a", "question": "m" * note_size}
    required = json.dumps({"tasks": [task], "mail": [mail], "scope": {}})
    sources = [
        {
            "id": "conversation",
            "version": "v1",
            "kind": "conversation",
            "current": True,
            "records": [{"id": str(n), "role": "user", "text": "x" * 5900} for n in range(30)],
        },
        {
            "id": "task-source",
            "version": "v1",
            "kind": "task",
            "records": [{"id": "0", "role": "task", "text": json.dumps(task)}],
        },
        {
            "id": "mail-source",
            "version": "v1",
            "kind": "inbox",
            "records": [{"id": "0", "role": "peer", "text": json.dumps(mail)}],
        },
    ]
    return {
        "memory_sources": sources,
        "required": required,
        "policy": "test",
        "source": {"scope_hash": "test"},
    }


def test_task_and_mail_bodies_cannot_be_cited_as_processed_evidence():
    plan = memory_continuity.plan(required_fixture(), None)
    assert {r["source"] for r in plan["selected"]} == {"conversation"}
    text = memory_continuity.prompt(plan)
    assert "t" * 2000 not in text and "m" * 2000 not in text
    data = json.loads(text.split("MEMORY UPDATE:\n", 1)[1])
    assert data["summary_input"]["reference_only_records"] == 2
    assert {r["source"] for r in data["reference_only"]} == {"task-source", "mail-source"}
    assert len(json.dumps(data["records"]).encode()) < memory_continuity.MAX_UPDATE_BYTES
    with pytest.raises(APIError, match="invalid or oversized"):
        memory_continuity.context(response(["task-source:v1:0", "mail-source:v1:0"]), plan)


def test_large_reference_only_work_does_not_starve_conversation_backlog():
    captured = required_fixture(50000)
    first = memory_continuity.plan(captured, None)
    context = json.loads(response(["conversation:v1:29"]))["context"]
    result = memory_continuity.result(first, context, [])
    assert result["update_complete"] and not result["gaps"]
    assert set(result["processed"]) == {"conversation"}
    assert result["summary_input"]["remaining_eligible_records"] > 0
    prior = {"continuity": result, "policy": "test", "source": captured["source"]}
    second = memory_continuity.plan(captured, prior)
    assert {r["source"] for r in second["selected"]} == {"conversation"}
    later = memory_continuity.result(second, context, [])
    assert later["summarized_records"] > result["summarized_records"]
    assert later["remaining_records"] > 2  # Reference-only originals and older conversation.


def test_indivisible_oversized_session_notes_stay_pending_with_readable_gap():
    captured = required_fixture(50000)
    captured["memory_sources"].append(
        {
            "id": "session-source",
            "version": "v1",
            "kind": "session",
            "records": [
                {"id": "0", "role": "current_facts", "text": json.dumps({"notes": "s" * 50000})}
            ],
        }
    )
    plan = memory_continuity.plan(captured, None)
    result = memory_continuity.result(plan, {}, [])
    assert not result["update_complete"]
    assert set(result["pending_updates"]) == {"session-source"}
    assert len(result["gaps"]) == 1
    assert "original remains readable" in result["gaps"][0]["reason"]
