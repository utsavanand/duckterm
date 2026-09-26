"""Oracle Relay end to end: notes appear for what needs the owner, answers
reach the right session by the right route, and rules act only as confirmed."""

import asyncio
import json
import time

import pytest
from tests.runtime.test_oracle import CLAUDE_DRAFT, CLAUDE_EMPTY, FakeSupervisor
from tests.runtime.test_session_api import dispatch, enroll

from duckterm.llm.summarizer import Summary
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

MENU = "Pick one color:\n❯ 1. Red\n  2. Green\n  3. Blue\nEnter to select"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    history.record(
        {
            "_id": "s0",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "pm",
            "test": True,
            "runtime": "claude-code",
            "name": "product-manager",
        }
    )
    history.set_meta("pm", name="product-manager", group="Duckterm")
    history.record({"_id": "s1", "_ts": 2, "event_type": "Stop", "session_key": "pm"})
    enroll(history, "pm", root="Duckterm")
    server = Server(history=history)
    sup = FakeSupervisor(CLAUDE_EMPTY)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: sup if key == "pm" else None)
    owner = {"x-duckterm-token": server.token}
    yield server, owner, sup, history
    history.close()


def post(server, headers, path, body):
    return dispatch(server, "POST", path, headers, json.dumps(body).encode())


def notes(server):
    return dispatch(server, "GET", "/relay", {})[1]["notes"]


def test_blocking_approval_becomes_a_note_and_the_answer_decides_it(world) -> None:
    server, owner, _, _ = world
    status, body = post(
        server,
        owner,
        "/approvals",
        {"session_key": "pm", "tool_name": "Bash", "tool_input": {"command": "pytest -q"}},
    )
    approval_id = body["id"]
    [note] = notes(server)
    assert (note["kind"], note["detail"], note["name"], note["status"]) == (
        "approval",
        "pytest -q",
        "product-manager",
        "open",
    )
    assert dispatch(server, "GET", "/relay/count", {})[1] == {"open": 1}
    assert post(server, {}, f"/relay/{note['id']}/answer", {"answer": "approve"})[0] == 401
    status, body = post(server, owner, f"/relay/{note['id']}/answer", {"answer": "approve"})
    assert status == 200 and body["note"]["status"] == "answered"
    assert server.approvals.decision_of(approval_id) == "approve"


def test_approval_rule_answers_blocking_requests_only_as_written(world) -> None:
    server, owner, _, _ = world
    rule = {
        "kind": "approval",
        "tool": "Bash",
        "command_pattern": r"^pytest\b",
        "folder": "Duckterm",
        "action": "approve",
    }
    assert post(server, owner, "/relay/rules", {"rule": rule})[1]["rule"]["id"] == "R1"
    ok = post(
        server,
        owner,
        "/approvals",
        {"session_key": "pm", "tool_name": "Bash", "tool_input": {"command": "pytest -q"}},
    )[1]["id"]
    chained = post(
        server,
        owner,
        "/approvals",
        {"session_key": "pm", "tool_name": "Bash", "tool_input": {"command": "pytest && rm -rf x"}},
    )[1]["id"]
    assert server.approvals.decision_of(ok) == "approve"
    assert server.approvals.decision_of(chained) is None
    by_detail = {n["detail"]: n for n in notes(server)}
    assert by_detail["pytest -q"]["answered_by"] == "R1"
    assert by_detail["pytest && rm -rf x"]["status"] == "open"
    assert dispatch(server, "DELETE", "/relay/rules/R1", owner)[0] == 200


def test_multiple_choice_answer_presses_the_option_only_while_its_menu_shows(world) -> None:
    server, owner, sup, _ = world
    ask = {
        "questions": [
            {
                "question": "Pick one color:",
                "options": [{"label": "Red"}, {"label": "Green"}, {"label": "Blue"}],
            }
        ]
    }
    server.bus.publish(
        {
            "event_type": "PermissionRequest",
            "session_key": "pm",
            "tool_name": "AskUserQuestion",
            "tool_input": ask,
        }
    )
    [note] = [n for n in notes(server) if n["kind"] == "choice"]
    assert note["options"] == ["Red", "Green", "Blue"]
    sup.screen = MENU
    status, body = post(server, owner, f"/relay/{note['id']}/answer", {"answer": 1})
    assert status == 200 and body["note"]["answer"] == "Green"
    assert sup.pasted == [b"2"]

    server.bus.publish(
        {
            "event_type": "PermissionRequest",
            "session_key": "pm",
            "tool_name": "AskUserQuestion",
            "tool_input": ask,
        }
    )
    [again] = [n for n in notes(server) if n["kind"] == "choice" and n["status"] == "open"]
    sup.screen = CLAUDE_EMPTY
    assert post(server, owner, f"/relay/{again['id']}/answer", {"answer": 0})[0] == 409
    assert sup.pasted == [b"2"]


BLOCKED = '{"kind": "blocked", "ask": "Should it spec the onboarding fix?", "options": []}'


def question_note(server, monkeypatch, text, verdict=BLOCKED, calls=None):
    """Run end-of-turn detection on a final message with a canned classifier."""
    server._RELAY_SETTLE_S = 0
    owner = {"role": "user", "blocks": [{"type": "text", "text": "review the onboarding"}]}
    reply = {"role": "assistant", "blocks": [{"type": "text", "text": text}]}
    monkeypatch.setattr(server, "_session_messages", lambda key: [owner, reply])

    def classify(prompt, claude_model=None):
        if calls is not None:
            calls.append((prompt, claude_model))
        return Summary(text=verdict, backend="cli" if verdict else "none")

    monkeypatch.setattr("duckterm.server.summarize", classify)
    asyncio.run(server._relay_detect_question("pm", int(time.time() * 1000)))
    return [n for n in notes(server) if n["kind"] == "question"]


def test_turn_ending_on_a_question_becomes_a_note_and_the_reply_is_typed(
    world, monkeypatch
) -> None:
    server, owner, sup, _ = world
    [note] = question_note(
        server, monkeypatch, "It makes a differentiator work. Want me to spec that fix?"
    )
    assert (note["question"], note["urgency"]) == ("Should it spec the onboarding fix?", "blocked")
    assert note["excerpt"].endswith("Want me to spec that fix?")
    status, body = post(server, owner, f"/relay/{note['id']}/answer", {"answer": "Yes, spec it"})
    assert (status, body["note"]["route"]) == (200, "prompt")
    assert sup.pasted == [b"\x1b[200~Yes, spec it\x1b[201~\r"]


def test_reply_goes_to_the_inbox_when_a_draft_blocks_typing(world, monkeypatch) -> None:
    server, owner, sup, history = world
    [note] = question_note(server, monkeypatch, "Should I start with B1?")
    sup.screen = CLAUDE_DRAFT
    status, body = post(server, owner, f"/relay/{note['id']}/answer", {"answer": "Yes"})
    assert (status, body["note"]["route"]) == (200, "inbox")
    assert "may be a draft" in body["note"]["route_reason"]
    assert sup.pasted == []
    assert [m["question"] for m in history.session_api.inbox("pm", owner=True)["messages"]] == [
        "Yes"
    ]


def test_owner_answering_in_the_terminal_closes_the_question_note(world, monkeypatch) -> None:
    server, _, _, _ = world
    [note] = question_note(server, monkeypatch, "Should I start with B1?")
    server.bus.publish({"event_type": "UserPromptSubmit", "session_key": "pm", "prompt": "yes"})
    assert [n["status"] for n in notes(server) if n["id"] == note["id"]] == ["handled"]


def test_answer_rule_prefills_a_draft_and_counts_unchanged_sends(world, monkeypatch) -> None:
    server, owner, _, _ = world
    rule = post(
        server,
        owner,
        "/relay/rules",
        {"rule": {"kind": "answer", "keywords": ["keep going"], "reply": "Yes, continue."}},
    )[1]["rule"]
    keep_going = '{"kind": "blocked", "ask": "Should it keep going with B2?", "options": []}'
    [note] = question_note(server, monkeypatch, "Should I keep going with B2?", keep_going)
    assert note["suggestion"] == {"rule_id": rule["id"], "reply": "Yes, continue."}
    post(server, owner, f"/relay/{note['id']}/answer", {"answer": "Yes, continue."})
    assert dispatch(server, "GET", "/relay", {})[1]["rules"][0]["streak"] == 1


def test_rule_proposal_comes_from_the_model_and_is_validated(world, monkeypatch) -> None:
    server, owner, _, _ = world
    replies = iter(
        [
            json.dumps(
                {
                    "kind": "approval",
                    "summary": "Approve pytest",
                    "tool": "Bash",
                    "command_pattern": r"^pytest\b",
                    "folder": "Duckterm",
                    "action": "approve",
                }
            ),
            '{"not_a_rule": true}',
        ]
    )
    monkeypatch.setattr(
        "duckterm.server.summarize", lambda prompt: Summary(text=next(replies), backend="cli")
    )
    status, body = post(
        server, owner, "/relay/rules/propose", {"text": "always approve pytest in Duckterm"}
    )
    assert status == 200 and body["rule"]["command_pattern"] == r"^pytest\b"
    assert dispatch(server, "GET", "/relay", {})[1]["rules"] == []  # proposing doesn't create
    assert post(server, owner, "/relay/rules/propose", {"text": "hello"})[0] == 422


def test_classifier_sees_the_owner_message_and_uses_sonnet(world, monkeypatch) -> None:
    server, _, _, _ = world
    calls: list = []
    question_note(server, monkeypatch, "Ready to merge to main on your word.", calls=calls)
    [(prompt, model)] = calls
    assert model == "sonnet"
    assert "review the onboarding" in prompt and "Ready to merge to main on your word." in prompt


def test_offer_shows_but_does_not_count_as_needing_you(world, monkeypatch) -> None:
    server, _, _, _ = world
    offer = '{"kind": "offer", "ask": "Want the quiz deck too?", "options": ["Yes", "No"]}'
    [note] = question_note(server, monkeypatch, "Done. Want the quiz deck too?", offer)
    assert (note["urgency"], note["options"]) == ("offer", ["Yes", "No"])
    assert dispatch(server, "GET", "/relay/count", {})[1] == {"open": 0}


def test_classifier_none_raises_no_note(world, monkeypatch) -> None:
    server, _, _, _ = world
    none = '{"kind": "none", "ask": "", "options": []}'
    assert (
        question_note(server, monkeypatch, "If the spacing still feels off, say where.", none) == []
    )


def test_turn_without_any_ask_cue_skips_the_model(world, monkeypatch) -> None:
    server, _, _, _ = world
    calls: list = []
    assert question_note(server, monkeypatch, "Deployed. All 19 tests passed.", calls=calls) == []
    assert calls == []


def test_owner_reply_during_the_settle_wait_skips_detection(world, monkeypatch) -> None:
    server, _, _, history = world
    calls: list = []
    history.record(
        {
            "_id": "u1",
            "_ts": int(time.time() * 1000) + 5_000,
            "event_type": "UserPromptSubmit",
            "session_key": "pm",
        }
    )
    assert question_note(server, monkeypatch, "Which do you want: 1 or 2?", calls=calls) == []
    assert calls == []


def test_without_a_model_the_question_mark_fallback_is_marked(world, monkeypatch) -> None:
    server, _, _, _ = world
    [note] = question_note(server, monkeypatch, "Should I start with B1?", verdict="")
    assert note["detected_without_model"] is True
    assert question_note(server, monkeypatch, "Tell me which batch is next.", verdict="") == [note]
