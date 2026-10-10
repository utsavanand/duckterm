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


def test_required_tasks_and_mail_stay_citable_with_large_conversation_backlog():
    captured = required_fixture()
    plan = memory_continuity.plan(captured, None)
    assert {r["source"] for r in plan["selected"]} == {"conversation", "task-source", "mail-source"}
    text = memory_continuity.prompt(plan, captured["required"])
    current = json.loads(
        text.split("CURRENT REQUIRED WORK:\n", 1)[1].split("\nMEMORY UPDATE:", 1)[0]
    )
    assert current["tasks"][0]["memory_ref"] == "task-source:v1:0"
    assert current["mail"][0]["memory_ref"] == "mail-source:v1:0"
    data = json.loads(text.split("MEMORY UPDATE:\n", 1)[1])
    assert len(json.dumps(data["records"]).encode()) < memory_continuity.MAX_UPDATE_BYTES
    context = memory_continuity.context(
        response([current["tasks"][0]["memory_ref"], current["mail"][0]["memory_ref"]]), plan
    )
    assert context["constraints"][0]["refs"] == ["task-source:v1:0", "mail-source:v1:0"]


def test_oversized_required_work_fails_instead_of_losing_its_evidence():
    with pytest.raises(APIError, match="Current work exceeds"):
        memory_continuity.plan(required_fixture(26000), None)
