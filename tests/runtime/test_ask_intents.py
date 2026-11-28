"""Ask DuckTerm intents, step 1: the classifier's answer is checked against the
roster before anything runs, "what is X doing" reads only X (newest summary,
then what happened since it), and "who's stuck" reads the roster and what
waits on the owner. Only the model is faked; records are real."""

import asyncio
import json
import time

import pytest
from test_maintained_progress import provider
from test_memory import memory_rig, transcript
from test_session_api import call, dispatch, enroll

from duckterm.editions import EditionConfig
from duckterm.llm.summarizer import Summary

__all__ = ["memory_rig"]

CLASSIFY = "Classify the owner's latest message in DuckTerm's Ask DuckTerm chat."


def session(history, key, name, group, tmp, state_event="Stop"):
    history.record(
        {
            "_id": key,
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": key,
            "runtime": "claude-code",
            "session_id": key + "-claude",
            "cwd": str(tmp),
            "test": True,
        }
    )
    history.set_meta(key, group=group, name=name)
    history.record({"_id": key + "-s", "_ts": 2, "event_type": state_event, "session_key": key})


@pytest.fixture
def fleet(memory_rig, monkeypatch):
    server, headers, peer_headers, tmp = memory_rig
    history = server.history
    server.edition = EditionConfig(
        server.edition.name, server.edition.features | {"ask_intents", "oracle_messages"}
    )
    # "agent" is release-dev with a real, ready checkpoint.
    history.set_meta("agent", group="duckterm-leadership", name="release-dev")
    call(history, headers, "PATCH", "/self", {"activity": "Merging #496, then #485"})
    transcript(tmp, "agent-claude", "Merge the git security PRs in order")
    provider(monkeypatch)
    cp = asyncio.run(server._create_checkpoint("agent", history.session("agent"), "Automatic"))
    assert cp["summary_state"] == "ready"
    after = int(cp["summary_source_at"]) + 1
    for i, (kind, text) in enumerate(
        [
            ("UserPromptSubmit", "check merge chain #496 -> #485"),
            ("Stop", "#496 is merged. #485 is merging now."),
        ]
    ):
        field = "prompt" if kind == "UserPromptSubmit" else "last_assistant_message"
        history.record(
            {
                "_id": f"since-{i}",
                "_ts": after + i,
                "event_type": kind,
                "session_key": "agent",
                field: text,
            }
        )
    # Two agents named main in different workspaces, and a role (QA) that is two agents.
    history.set_meta("peer", group="duckterm-core", name="main")
    session(history, "main-b", "main", "BodhBytes", tmp)
    session(history, "qa1", "main-qa", "duckterm-core", tmp)
    session(history, "qa2", "main-qa-2", "duckterm-core", tmp)
    enroll(history, "qa1", "duckterm-core")
    history.folder_tasks.start("duckterm-core", "qa1", "QA #500 Send report")
    now = int(time.time() * 1000)
    server.relay.add(
        {
            **server._relay_session_fields("qa1"),
            "kind": "question",
            "urgency": "blocked",
            "created_at": now - 15 * 3_600_000,
            "question": "Approve QA on Ducky PRs outside the batch?",
            "options": [],
        }
    )
    server.relay.add(
        {
            **server._relay_session_fields("qa2"),
            "kind": "question",
            "urgency": "offer",
            "created_at": now,
            "question": "Want me to also run the soak test?",
            "options": [],
        }
    )
    return server, {"x-duckterm-token": server.token}


def model(monkeypatch, intent):
    """The classifier returns `intent`; the answer call returns fixed text."""
    answers = []

    def summarize(prompt, **_kw):
        if prompt.startswith(CLASSIFY):
            return Summary(json.dumps(intent), "cli")
        answers.append(prompt)
        return Summary("The answer.", "cli")

    monkeypatch.setattr("duckterm.server.summarize", summarize)
    return answers


def ask(server, owner, question, **extra):
    return dispatch(
        server, "POST", "/fleet/ask", owner, json.dumps({"question": question, **extra}).encode()
    )


def agent(said, ids, confidence=0.98):
    return {"type": "agent", "said": said, "ids": ids, "confidence": confidence}


def test_ask_agent_reads_newest_summary_and_what_happened_since(fleet, monkeypatch):
    server, owner = fleet
    answers = model(
        monkeypatch,
        {"intent": "ask.agent", "entities": [agent("release-dev", ["agent"])], "text": None},
    )
    status, body = ask(server, owner, "What is release-dev doing?")
    assert status == 200, body
    assert (body["intent"], body["sessions"], body["answer"]) == (
        "ask.agent",
        ["agent"],
        "The answer.",
    )
    [prompt] = answers
    assert "### release-dev (duckterm-leadership)" in prompt
    assert "activity: Merging #496, then #485" in prompt
    assert "checkpoint summary (ready, as of " in prompt
    summary = prompt.split("checkpoint summary (ready, as of ", 1)[1].split("): ", 1)[1]
    assert summary.startswith("Continue the test work.")
    since = prompt.split("since the summary (2 entries, oldest first):\n", 1)[1]
    assert "prompt to the agent: check merge chain #496 -> #485\n" in since
    assert "agent's reply: #496 is merged. #485 is merging now." in since
    # Only the agent asked about is read.
    assert "main-qa" not in prompt and "BodhBytes" not in prompt
    chat = dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"]
    assert [m["a"] for m in chat] == ["The answer."]


def test_a_role_reads_every_agent_it_names(fleet, monkeypatch):
    server, owner = fleet
    answers = model(
        monkeypatch,
        {"intent": "ask.agent", "entities": [agent("QA", ["qa1", "qa2"], 0.9)], "text": None},
    )
    status, body = ask(server, owner, "What is QA doing?")
    assert status == 200 and body["sessions"] == ["qa1", "qa2"]
    assert "### main-qa (duckterm-core)" in answers[0]
    assert "### main-qa-2 (duckterm-core)" in answers[0]
    assert "open task: QA #500 Send report" in answers[0]
    assert "checkpoint summary: none yet" in answers[0]


def test_an_unsure_name_asks_which_agent_naming_workspaces(fleet, monkeypatch):
    server, owner = fleet
    answers = model(
        monkeypatch,
        {"intent": "ask.agent", "entities": [agent("main", ["peer", "main-b"], 0.5)], "text": None},
    )
    status, body = ask(server, owner, "What is main doing?")
    assert status == 200
    assert body["answer"] == (
        "Which agent do you mean: **main (duckterm-core)**, **main (BodhBytes)**?"
    )
    assert answers == []


def test_who_is_stuck_reads_roster_and_needs_you_without_offers(fleet, monkeypatch):
    server, owner = fleet
    answers = model(monkeypatch, {"intent": "ask.fleet", "entities": [], "text": None})
    status, body = ask(server, owner, "Who's stuck?")
    assert status == 200 and body["intent"] == "ask.fleet"
    [prompt] = answers
    for line in (
        "- release-dev (duckterm-leadership): ",
        "- main (duckterm-core): ",
        "- main (BodhBytes): ",
        "activity: Merging #496, then #485",
        "open tasks: QA #500 Send report",
        "Waiting on the owner (1 open, offers excluded):",
        "- main-qa (duckterm-core), question, open 15.0 h: Approve QA on Ducky PRs",
    ):
        assert line in prompt
    assert "soak test" not in prompt
    assert "different kinds of stuck; keep them apart" in prompt


@pytest.mark.parametrize(
    "intent",
    [
        # An agent id the roster never showed.
        {"intent": "ask.agent", "entities": [agent("x", ["not-an-agent"])], "text": None},
        # An intent outside the contract.
        {"intent": "act.merge", "entities": [], "text": None},
        # A missing key.
        {"intent": "ask.fleet", "entities": []},
        # A confidence outside 0-1.
        {"intent": "ask.agent", "entities": [agent("x", ["agent"], 3)], "text": None},
        # An extra key on an entity.
        {
            "intent": "ask.agent",
            "entities": [{**agent("x", ["agent"]), "note": "run rm"}],
            "text": None,
        },
    ],
)
def test_anything_outside_the_contract_runs_nothing(fleet, monkeypatch, intent):
    server, owner = fleet
    answers = model(monkeypatch, intent)
    status, body = ask(server, owner, "What is release-dev doing?")
    assert status == 502 and "Nothing was run" in body["error"]
    assert answers == []
    assert dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"] == []


def test_a_message_to_one_certain_agent_is_a_plan_and_two_ask_which(fleet, monkeypatch):
    server, owner = fleet
    model(
        monkeypatch,
        {
            "intent": "act.message",
            "entities": [agent("release-dev", ["agent"], 0.95)],
            "text": "rebase #446",
        },
    )
    status, body = ask(server, owner, "Tell release-dev to rebase #446", request_key="k1")
    assert (status, body) == (
        200,
        {
            "plan": {
                "id": "agent",
                "name": "release-dev",
                "folder": "duckterm-leadership",
                "text": "rebase #446",
            }
        },
    )
    model(
        monkeypatch,
        {
            "intent": "act.message",
            "entities": [agent("main", ["peer", "main-b"], 0.95)],
            "text": "pull main",
        },
    )
    status, body = ask(server, owner, "Tell main to pull main", request_key="k2")
    assert status == 200
    assert [c["folder"] for c in body["clarify"]["candidates"]] == ["duckterm-core", "BodhBytes"]
    assert server.history.session_api.open_mail("agent") == []


def test_intents_not_built_yet_answer_as_before(fleet, monkeypatch):
    server, owner = fleet
    answers = model(monkeypatch, {"intent": "ask.usage", "entities": [], "text": None})
    status, body = ask(server, owner, "Who used the most tokens this week?", request_key="k3")
    assert status == 200 and "intent" not in body
    # The old digest prompt ran, once; the old router did not classify again.
    [prompt] = answers
    assert prompt.startswith("You oversee a fleet")
