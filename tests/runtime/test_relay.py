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

MENU = "Merge PR #66?\n❯ 1. Yes\n  2. Hold\nEnter to select"


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


def test_menu_form_is_one_note_listing_every_question_and_is_answered_in_the_terminal(
    world,
) -> None:
    server, owner, sup, _ = world
    ask = {
        "questions": [
            {"question": "Merge PR #66?", "options": [{"label": "Yes"}, {"label": "Hold"}]},
            {
                "question": "Which rule applies?",
                "multiSelect": True,
                "options": [{"label": "release-dev releases"}, {"label": "Devs release"}],
            },
        ]
    }
    event = {
        "event_type": "PermissionRequest",
        "session_key": "pm",
        "tool_name": "AskUserQuestion",
        "tool_input": ask,
    }
    server.bus.publish(event)
    server.bus.publish(event)  # a repeat while the note is open adds nothing
    [note] = [n for n in notes(server) if n["kind"] == "choice"]
    assert note["questions"] == [
        {"question": "Merge PR #66?", "options": ["Yes", "Hold"]},
        {"question": "Which rule applies?", "options": ["release-dev releases", "Devs release"]},
    ]
    sup.screen = MENU
    status, body = post(server, owner, f"/relay/{note['id']}/answer", {"answer": 0})
    assert status == 409 and "in its terminal" in body["error"]
    assert sup.pasted == []

    server.bus.publish({"event_type": "PostToolUse", "session_key": "pm", "tool_name": "X"})
    assert [n["status"] for n in notes(server) if n["kind"] == "choice"] == ["handled"]


BLOCKED = '{"kind": "blocked", "ask": "Should it spec the onboarding fix?", "options": []}'


def question_note(server, monkeypatch, text, verdict=BLOCKED, calls=None):
    """Run end-of-turn detection on a final message with a canned classifier."""
    server._RELAY_SETTLE_S = 0
    owner = {"role": "user", "blocks": [{"type": "text", "text": "review the onboarding"}]}
    reply = {"role": "assistant", "blocks": [{"type": "text", "text": text}]}
    monkeypatch.setattr(server, "_read_messages", lambda source: [owner, reply])

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
    assert sup.pasted == [b"\x1b[200~Yes, spec it\x1b[201~", b"\r"]


class SlowTerminal(FakeSupervisor):
    """Claude Code as seen in the wild: the paste lands in the prompt, and an
    Enter that arrives too soon after it is swallowed. It submits (records
    UserPromptSubmit and clears the prompt) only on Enter number `submits_on`."""

    def __init__(self, history, submits_on: int | None) -> None:
        super().__init__(CLAUDE_EMPTY)
        self.history, self.submits_on, self.enters = history, submits_on, 0

    def write_bytes(self, data: bytes) -> bool:
        self.pasted.append(data)
        if data.startswith(b"\x1b[200~"):
            text = data[len(b"\x1b[200~") : -len(b"\x1b[201~")].decode()
            self.screen = CLAUDE_EMPTY.replace("❯\xa0", "❯\xa0" + text)
        elif data == b"\r":
            self.enters += 1
            if self.enters == self.submits_on:
                self.screen = CLAUDE_EMPTY
                now = int(time.time() * 1000)
                self.loop.call_soon_threadsafe(
                    self.history.record,
                    {
                        "_id": f"ups-{now}",
                        "_ts": now,
                        "event_type": "UserPromptSubmit",
                        "session_key": "pm",
                    },
                )
        return True


def test_swallowed_enter_is_pressed_again_until_the_agent_takes_the_prompt(
    world, monkeypatch
) -> None:
    server, owner, _, history = world
    slow = SlowTerminal(history, submits_on=2)
    original_submit = server._submit_prompt

    async def submit(key, text):
        # Real hooks are ingested on the server loop, not the terminal writer.
        slow.loop = asyncio.get_running_loop()
        return await original_submit(key, text)

    monkeypatch.setattr(server, "_submit_prompt", submit)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: slow)
    [note] = question_note(server, monkeypatch, "Should I start with B1?")
    status, body = post(server, owner, f"/relay/{note['id']}/answer", {"answer": "Yes"})
    assert (status, body["note"]["route"]) == (200, "prompt")
    assert slow.pasted == [b"\x1b[200~Yes\x1b[201~", b"\r", b"\r"]


def test_reply_that_never_submits_is_reported_stuck(world, monkeypatch) -> None:
    server, owner, _, history = world
    stuck = SlowTerminal(history, submits_on=None)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: stuck)
    [note] = question_note(server, monkeypatch, "Should I start with B1?")
    status, body = post(server, owner, f"/relay/{note['id']}/answer", {"answer": "Yes"})
    assert (status, body["note"]["route"]) == (200, "prompt-stuck")
    assert stuck.pasted.count(b"\r") == 2  # one retry, then it stops pressing


def test_owner_message_that_never_submits_says_to_press_enter(world, monkeypatch) -> None:
    server, owner, _, history = world
    stuck = SlowTerminal(history, submits_on=None)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: stuck)
    body = json.dumps({"text": "go ahead", "mode": "prompt"}).encode()
    status, reply = dispatch(server, "POST", "/sessions/pm/message", owner, body)
    assert status == 409 and "Press Enter in its terminal" in reply["error"]


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


# Codex 0.155's own approval prompt (captured from a live session, path trimmed).
CODEX_APPROVAL = (
    "• Running touch probe-file.txt\n  Would you like to run the following command?\n"
    "  $ touch probe-file.txt\n› 1. Yes, proceed (y)\n"
    "  2. Yes, and don't ask again for commands that start with `touch` (p)\n"
    "  3. No, and tell Codex what to do differently (esc)\n"
)
# A real busy Codex screen carries its status line with a timer; the command
# line alone ("• Running ...") reads like prose and no longer counts as busy.
CODEX_WORKING = (
    "• Running touch probe-file.txt\n\n• Working (8s • esc to interrupt)\n\n"
    "› Ask Codex to do anything\n"
)


@pytest.fixture
def codex_world(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    history.record(
        {
            "_id": "c0",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "cx",
            "test": True,
            "runtime": "codex",
            "name": "feature-remote-session",
        }
    )
    server = Server(history=history)
    server._RELAY_WATCH_EVERY_S = 0.01
    sup = FakeSupervisor(CODEX_WORKING)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: sup if key == "cx" else None)
    yield server, {"x-duckterm-token": server.token}, sup
    history.close()


def request(server, command="touch probe-file.txt"):
    server.bus.publish(
        {
            "event_type": "PermissionRequest",
            "session_key": "cx",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )


def test_codex_request_its_reviewer_approves_never_becomes_a_note(codex_world) -> None:
    server, _, _ = codex_world

    async def scenario():
        request(server)
        await asyncio.sleep(0.05)  # the watcher looks; no prompt on screen
        assert server.relay.notes == []
        server.bus.publish({"event_type": "PostToolUse", "session_key": "cx", "tool_name": "Bash"})
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert notes(server) == []
    assert server.approvals.pending() == []


def test_codex_request_becomes_a_note_once_its_prompt_shows_and_approve_presses_y(
    codex_world,
) -> None:
    server, owner, sup = codex_world

    async def scenario():
        request(server)
        await asyncio.sleep(0.05)
        assert server.relay.notes == []
        sup.screen = CODEX_APPROVAL
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    [note] = notes(server)
    assert (note["kind"], note["detail"], note["blocking"]) == (
        "approval",
        "touch probe-file.txt",
        False,
    )
    status, body = post(server, owner, f"/relay/{note['id']}/answer", {"answer": "approve"})
    assert (status, body["note"]["route"]) == (200, "keystroke")
    assert sup.pasted == [b"y"]
    assert server.approvals.pending() == []


def test_codex_approval_answered_after_its_prompt_left_presses_nothing(codex_world) -> None:
    server, owner, sup = codex_world

    async def scenario():
        request(server)
        sup.screen = CODEX_APPROVAL
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    [note] = notes(server)
    sup.screen = CODEX_WORKING
    assert post(server, owner, f"/relay/{note['id']}/answer", {"answer": "deny"})[0] == 409
    assert sup.pasted == []


def test_old_codex_hooks_cannot_register_a_waiting_approval(codex_world) -> None:
    server, owner, _ = codex_world
    status, body = post(
        server,
        owner,
        "/approvals",
        {"session_key": "cx", "tool_name": "Bash", "tool_input": {"command": "ls"}},
    )
    assert (status, body) == (200, {"id": None})
    assert server.approvals.pending() == []


def state(server, key="cx"):
    return server.history.session(key)["state"]


def test_codex_request_keeps_it_busy_until_its_prompt_shows(codex_world) -> None:
    server, _, sup = codex_world

    async def scenario():
        request(server)
        await asyncio.sleep(0.05)
        assert state(server) == "busy"  # its reviewer is running the command
        sup.screen = CODEX_APPROVAL
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert state(server) == "waiting"
    assert [n["kind"] for n in notes(server)] == ["approval"]


def test_claude_permission_request_still_means_waiting(world) -> None:
    server, _, _, _ = world
    server.bus.publish(
        {
            "event_type": "PermissionRequest",
            "session_key": "pm",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf build"},
        }
    )
    assert state(server, "pm") == "waiting"


LATER = 3 * 60_000  # past the stuck threshold


def test_prompt_that_shows_after_the_watch_ends_still_becomes_a_note(codex_world) -> None:
    server, _, sup = codex_world
    request(server)  # no event loop, so no watcher: as if its 10 minutes ran out
    sup.screen = CODEX_APPROVAL
    now = int(time.time() * 1000)
    asyncio.run(server._relay_check_stuck(now + LATER))
    assert [n["kind"] for n in notes(server)] == ["approval"]
    assert state(server) == "waiting"


def test_unknown_prompt_shape_shows_waiting_and_logs_its_screen_once(codex_world, tmp_path) -> None:
    server, _, sup = codex_world
    server.history.set_meta("cx", name="feature-remote-session")
    request(server, "curl https://example.com")
    now = int(time.time() * 1000)
    asyncio.run(server._relay_check_stuck(now + 30_000))  # too early to judge
    assert state(server) == "busy"
    sup.screen = "Allow network access to example.com?\n  a. Allow once\n  d. Deny\n"
    asyncio.run(server._relay_check_stuck(now + LATER))
    asyncio.run(server._relay_check_stuck(now + LATER + 60_000))
    assert state(server) == "waiting"
    assert notes(server) == []  # Oracle can't answer a shape it can't read
    [entry] = json.loads((tmp_path / "relay-missed-prompts.json").read_text())
    assert (entry["session"], entry["runtime"], entry["detail"]) == (
        "feature-remote-session",
        "codex",
        "curl https://example.com",
    )
    assert "Allow network access to example.com?" in entry["screen"]


def test_busy_codex_screen_is_not_logged_as_a_missed_prompt(codex_world, tmp_path) -> None:
    server, _, _ = codex_world
    request(server)
    asyncio.run(server._relay_check_stuck(int(time.time() * 1000) + LATER))
    assert state(server) == "busy"
    assert not (tmp_path / "relay-missed-prompts.json").exists()


def test_dashboard_stream_carries_the_auto_reviewed_tag(codex_world) -> None:
    """The dashboard folds live events itself; without the tag it would show
    every Codex request as waiting even though the server keeps it busy."""
    server, _, _ = codex_world

    async def scenario():
        with server.bus.subscribe() as feed:
            request(server)
            return await asyncio.wait_for(feed.next(), 1)

    streamed = asyncio.run(scenario())
    assert (streamed["event_type"], streamed.get("auto_reviewed")) == ("PermissionRequest", True)
