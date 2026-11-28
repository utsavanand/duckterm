"""Ask DuckTerm intents, step 1: the classifier's answer is checked against the
roster before anything runs, "what is X doing" reads only X (newest summary,
then what happened since it), and "who's stuck" reads the roster and what
waits on the owner. Only the model is faked; records are real."""

import asyncio
import base64
import json
import time

import pytest
from test_maintained_progress import provider
from test_memory import memory_rig, transcript
from test_session_api import call, dispatch, enroll

from duckterm.core.tokens import TokenLedger
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
    # Token usage reads transcripts; never the developer's own.
    server._tokens = TokenLedger(tmp / "no-claude", tmp / "no-codex")
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
    history._conn.commit()
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
    answers = model(monkeypatch, {"intent": "ask.history", "entities": [], "text": None})
    status, body = ask(server, owner, "What shipped yesterday?", request_key="k3")
    assert status == 200 and "intent" not in body
    # The old digest prompt ran, once; the old router did not classify again.
    [prompt] = answers
    assert prompt.startswith("You oversee a fleet")


# ── Step 2: list answers, no answer call ──


def listed(server, owner, monkeypatch, intent, question):
    answers = model(monkeypatch, {"text": None, **intent})
    status, body = ask(server, owner, question)
    assert status == 200, body
    assert answers == [] and body["intent"] == intent["intent"]
    return body["answer"]


def test_needs_you_lists_open_questions_oldest_first_without_offers(fleet, monkeypatch):
    server, owner = fleet
    server.relay.add(
        {
            **server._relay_session_fields("main-b"),
            "kind": "question",
            "urgency": "blocked",
            "created_at": int(time.time() * 1000) - 6 * 86_400_000,
            "question": "Central thought behind The Great Divide?",
            "options": [],
        }
    )
    answer = listed(server, owner, monkeypatch, {"intent": "ask.needs_you", "entities": []}, "?")
    lines = answer.splitlines()
    assert lines[0] == "2 open, oldest first (offers not counted):"
    assert lines[1].startswith("- **main** (BodhBytes) · question · open 6 d: Central thought")
    assert lines[2].startswith("- **main-qa** (duckterm-core) · question · open 15 h: Approve")
    assert "soak test" not in answer


def test_artifacts_match_titles_behind_the_500_row_limit_and_removed_ones(fleet, monkeypatch):
    server, owner = fleet
    store = server.history.artifacts
    content = base64.b64encode(b"# spec").decode()

    def register(key, title, path):
        return store.register(
            key, {"title": title, "source_path": path, "content_base64": content, "kind": "spec"}
        )

    old = register("peer", "PR review platform spec (private)", "/w/prp.md")
    gone = register("peer", "PR review platform spec v0", "/w/prp0.md")
    store.remove("peer", gone["id"])
    # 600 newer artifacts would push the spec past list_folder's 500 rows.
    filler = register("qa1", "Release notes", "/w/notes.md")
    conn = server.history._conn
    with conn:
        conn.execute("UPDATE artifacts SET updated_at = 1000 WHERE id = ?", (old["id"],))
        for i in range(600):
            conn.execute(
                "INSERT INTO artifacts SELECT ? , session_key, title, source_path || ?, "
                "media_type, size, sha256, created_at, updated_at + ?, content FROM artifacts "
                "WHERE id = ?",
                (f"filler-{i}", str(i), i, filler["id"]),
            )
    assert old["id"] not in {r["id"] for r in store.list_folder("duckterm-core", 500)}
    intent = {
        "intent": "ask.artifacts",
        "entities": [
            {
                "type": "artifact",
                "said": "the PR review platform spec",
                "id": None,
                "confidence": 0.9,
            },
            agent("main", ["main-b"], 0.5),
        ],
    }
    answer = listed(
        server, owner, monkeypatch, intent, "Did main make the PR review platform spec?"
    )
    lines = answer.splitlines()
    assert lines[0] == "None of these are by main (BodhBytes)."
    assert lines[1] == "2 artifact(s) with titles matching “review platform”, newest first:"
    assert "“PR review platform spec v0” by **main** (duckterm-core)" in lines[2]
    assert "· removed " in lines[2]
    assert lines[3].startswith("- “PR review platform spec (private)” by **main** (duckterm-core)")


def test_work_counts_in_progress_with_stale_ones_apart(fleet, monkeypatch):
    server, owner = fleet
    tasks = server.history.folder_tasks
    stale = tasks.start("BodhBytes", "main-b", "Old feature nobody touched")
    with server.history._conn as conn:
        conn.execute("UPDATE folder_tasks SET updated_at = 1 WHERE id = ?", (stale["id"],))
    answer = listed(server, owner, monkeypatch, {"intent": "ask.work", "entities": []}, "?")
    lines = answer.splitlines()
    assert lines[0] == (
        "2 tasks are marked in progress: 1 updated in the last 7 days, 1 not touched for "
        "longer (counted, not listed)."
    )
    assert lines[1].startswith("- QA #500 Send report · **main-qa** (duckterm-core) · updated")
    assert "Old feature" not in answer


def test_usage_ranks_agents_with_cache_reads_apart(fleet, monkeypatch, tmp_path):
    server, owner = fleet
    claude = tmp_path / "usage" / "projects"
    server._tokens = TokenLedger(claude, tmp_path / "usage" / "sessions")
    session(server.history, "spender", "spender", "duckterm-core", tmp_path)
    with server.history._conn as conn:
        conn.execute("UPDATE sessions SET test = 0 WHERE session_key = 'spender'")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = []
    for i, (inp, out, write, read) in enumerate(
        [(1000, 500, 0, 900_000), (2_000_000, 300_000, 0, 10)]
    ):
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "spender-claude",
                    "timestamp": now,
                    "requestId": f"r{i}",
                    "message": {
                        "id": f"m{i}",
                        "model": "claude-opus-5-5",
                        "usage": {
                            "input_tokens": inp,
                            "output_tokens": out,
                            "cache_creation_input_tokens": write,
                            "cache_read_input_tokens": read,
                        },
                    },
                }
            )
        )
    path = claude / "p" / "spender-claude.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines) + "\n")
    answer = listed(server, owner, monkeypatch, {"intent": "ask.usage", "entities": []}, "?")
    assert "over the last 7 day(s) (UTC days" in answer
    assert "- **spender** (duckterm-core): 2.3M (output 0.3M) · cache reads 0.9M" in answer


def test_health_is_a_checklist_of_recent_problems(fleet, monkeypatch, tmp_path):
    server, owner = fleet
    session(server.history, "crashed", "crashed", "duckterm-core", tmp_path)
    session(server.history, "old-crash", "old-crash", "BodhBytes", tmp_path)
    with server.history._conn as conn:
        conn.execute(
            "UPDATE sessions SET state = 'interrupted' WHERE session_key IN (?, ?)",
            ("crashed", "old-crash"),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_key = 'crashed'",
            (int(time.time() * 1000),),
        )
    answer = listed(server, owner, monkeypatch, {"intent": "ask.health", "entities": []}, "?")
    assert answer.startswith("Checked: checkpoints of agents active in the last 7 days")
    assert "- Waiting on you: 1 open question(s)." in answer
    assert (
        "- Interrupted in the last 24 hours: 1.\n  - **crashed** (duckterm-core), 0 h ago" in answer
    )
    assert "old-crash" not in answer


def test_sent_lists_owner_messages_with_unanswered_counted(fleet, monkeypatch):
    server, owner = fleet
    api = server.history.session_api
    api.owner_message("qa1", "Is #500 QA done?", question=True, request_key="ask-1")
    api.owner_message("qa2", "Run the soak test", question=True, request_key="ask-2")
    answer = listed(server, owner, monkeypatch, {"intent": "ask.sent", "entities": []}, "?")
    lines = answer.splitlines()
    assert lines[0].startswith(
        "2 message(s) sent through DuckTerm in the last 7 days, 2 still unanswered"
    )
    assert "to **main-qa-2** (duckterm-core)" in lines[1] and "“Run the soak test”" in lines[2]
    assert "to **main-qa** (duckterm-core)" in lines[3]
