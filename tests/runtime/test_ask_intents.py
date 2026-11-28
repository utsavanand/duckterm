"""Ask DuckTerm intents, step 1: the classifier's answer is checked against the
roster before anything runs, "what is X doing" reads only X (newest summary,
then what happened since it), and "who's stuck" reads the roster and what
waits on the owner. Only the model is faked; records are real."""

import asyncio
import base64
import json
import subprocess
import time

import pytest
from test_maintained_progress import provider
from test_memory import memory_rig, transcript
from test_session_api import call, dispatch, enroll

from duckterm.core import ask_intents
from duckterm.core.tokens import TokenLedger
from duckterm.editions import EditionConfig
from duckterm.llm.summarizer import Summary

__all__ = ["memory_rig"]

CLASSIFY = "Classify the owner's latest message in DuckTerm's Ask DuckTerm chat."
RERANK = "Which of these numbered snippets are actually about"


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
    # The rig flags its sessions as tests; here they stand in for real agents,
    # and Ask DuckTerm leaves test sessions out.
    history._conn.execute("UPDATE sessions SET test = 0")
    history._conn.commit()
    return server, {"x-duckterm-token": server.token}


def model(monkeypatch, intent, keep=None):
    """The classifier returns `intent`; the reranker keeps `keep` (a JSON
    value, or raw text); the answer call returns fixed text."""
    answers = []

    def summarize(prompt, **_kw):
        if prompt.startswith(CLASSIFY):
            return Summary(json.dumps(intent), "cli")
        answers.append(prompt)
        if prompt.startswith(RERANK):
            return Summary(keep if isinstance(keep, str) else json.dumps({"keep": keep}), "cli")
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


def test_latest_checkpoint_resolves_without_reading_retained_sources(fleet):
    server, _ = fleet
    history = server.history
    whole = history.checkpoints("agent")[0]
    with history._conn as conn:
        conn.execute(
            "UPDATE checkpoints SET record_json = json_set(record_json, '$.retained_sources', "
            "json(?)) WHERE session_key = 'agent'",
            (json.dumps([{"id": "s", "text": "x" * 1_000_000}]),),
        )
    latest = history.latest_checkpoint("agent")
    assert latest["summary"] == whole["summary"] and latest["summary"]
    assert latest["summary_state"] == whole["summary_state"] == "ready"
    assert "retained_sources" not in latest["record"]
    with history._conn as conn:
        conn.execute("UPDATE checkpoints SET record_json = '{' WHERE session_key = 'agent'")
    assert history.latest_checkpoint("agent")["summary_state"] == "unavailable"


def test_the_classify_call_is_bounded_whatever_the_fleet_or_paste(fleet, monkeypatch):
    server, _ = fleet
    monkeypatch.setattr("duckterm.core.ask_intents.ROSTER_BYTES", 600)
    with server.history._conn as conn:
        conn.execute("UPDATE sessions SET state = 'stopped' WHERE session_key = 'main-b'")
    agents = ask_intents.roster(server)
    assert 0 < len(agents) < 5 and "main-b" not in {a["id"] for a in agents}
    assert len(json.dumps(agents)) <= 600
    prompt = ask_intents.classify_prompt("status? " + "x" * 50_000, agents, [])
    owner_message = json.loads(prompt.split("\n")[-1])["owner_message"]
    assert len(owner_message) == ask_intents.CLASSIFY_QUESTION_BYTES


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


def test_a_message_goes_only_to_a_running_agent(fleet, monkeypatch):
    server, owner = fleet
    with server.history._conn as conn:
        conn.execute(
            "UPDATE sessions SET state = 'stopped', updated_at = ? WHERE session_key = 'main-b'",
            (int(time.time() * 1000),),
        )
    model(
        monkeypatch,
        {
            "intent": "act.message",
            "entities": [agent("main", ["main-b"], 0.95)],
            "text": "pull main",
        },
    )
    status, body = ask(server, owner, "Tell main in BodhBytes to pull main", request_key="k1")
    assert status == 200 and "plan" not in body
    assert body["answer"] == "main (BodhBytes) isn't running, so nothing was sent."
    # With a running namesake, the stopped one drops out and the plan is certain.
    model(
        monkeypatch,
        {
            "intent": "act.message",
            "entities": [agent("main", ["peer", "main-b"], 0.95)],
            "text": "pull main",
        },
    )
    status, body = ask(server, owner, "Tell main to pull main", request_key="k2")
    assert body["plan"]["id"] == "peer"


def test_intents_not_built_yet_answer_as_before(fleet, monkeypatch):
    server, owner = fleet
    answers = model(monkeypatch, {"intent": "act.rule", "entities": [], "text": None})
    status, body = ask(server, owner, "Always let QA's reads through", request_key="k3")
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


def test_thousands_of_classifier_terms_stay_under_the_sql_parameter_limit(fleet, monkeypatch):
    server, owner = fleet
    content = base64.b64encode(b"# spec").decode()
    server.history.artifacts.register(
        "peer",
        {
            "title": "Mono lab spec",
            "source_path": "/w/m.md",
            "content_base64": content,
            "kind": "spec",
        },
    )
    terms = ["mono lab spec", *(f"term{i} word{i} part{i} more{i}" for i in range(5000))]
    intent = {
        "intent": "ask.artifacts",
        "entities": [{"type": "topic", "said": "spec", "terms": terms, "confidence": 0.9}],
    }
    answer = listed(server, owner, monkeypatch, intent, "Where's the Mono lab spec?")
    assert "“Mono lab spec” by **main** (duckterm-core)" in answer
    many = [[f"w{i}-{j}" for j in range(40)] for i in range(2000)]
    assert server.history.artifacts.find_titles(many) == []


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


def test_health_is_a_checklist_of_recent_problems(fleet, monkeypatch, tmp_path, github):
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
    # The checklist never builds the PR list; it reads a copy a PR question built.
    assert "- Pull requests: not read; ask which PRs need you to read them." in answer
    assert len(github) == 0
    asyncio.run(server._pr_copy())  # each test request runs its own event loop
    answer = listed(server, owner, monkeypatch, {"intent": "ask.health", "entities": []}, "?")
    assert "): 1 with merge conflicts, 1 with CI failing, of 4 open." in answer


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


# ── Step 3: history ──

DAY = 86_400_000


def chunk(history, key, prose, at, identity=None):
    """A summary chunk as the checkpoint writer stores it (only the fields read)."""
    identity = identity or f"chunk-{key}-{at}"
    with history._conn as conn:
        conn.execute(
            "INSERT INTO digest_items (id, session_key, bucket, text, created_at, updated_at) "
            "VALUES (?, ?, 'summary_revision_v1', ?, ?, ?)",
            (identity, key, json.dumps({"summary": prose, "source_at": at}), at, at),
        )
    return identity


def memory(history, key, items, at):
    with history._conn as conn:
        conn.execute(
            "INSERT INTO digest_items (id, session_key, bucket, text, created_at, updated_at) "
            "VALUES (?, ?, 'memory_items_v1', ?, ?, ?)",
            (
                f"mem-{key}-{at}",
                key,
                json.dumps({"format": "memory_items_v1", "items": items}),
                at,
                at,
            ),
        )


def topic(said, *terms):
    return {"type": "topic", "said": said, "terms": list(terms), "confidence": 0.9}


def test_history_ranks_distilled_whole_phrase_records_first_with_recorded_dates(fleet, monkeypatch):
    server, owner = fleet
    history = server.history
    now = int(time.time() * 1000)
    old = chunk(history, "peer", "Reviewed the approval flow.", now - 10 * DAY)
    new = chunk(history, "qa1", "Approval follow-up.", now - 2 * DAY)
    memory(
        history,
        "peer",
        [
            {
                "id": "a",
                "kind": "decision",
                "text": "Owner rejected Touch ID for approvals",
                "since": old,
            }
        ],
        now - 10 * DAY,
    )
    memory(
        history,
        "qa1",
        [
            {
                "id": "b",
                "kind": "decision",
                "text": "Owner chose Touch ID as the owner-only boundary",
                "since": new,
            }
        ],
        now - 2 * DAY,
    )
    # Substring recall brings this in ("face" in "interface"); ranking puts it last.
    chunk(history, "qa2", "Polished the interface of the face panel.", now - DAY)
    intent = {"intent": "ask.history", "entities": [topic("Touch ID", "Face ID")], "text": None}
    answers = model(monkeypatch, intent, keep=[0, 1])
    status, body = ask(server, owner, "When did we decide Touch ID?")
    assert status == 200 and body["intent"] == "ask.history"
    rerank, answer = answers
    snippets = json.loads(rerank.split("\n", 1)[1])["snippets"]
    assert [s["source"] for s in snippets[:2]] == ["memory decision", "memory decision"]
    assert snippets[-1]["text"].startswith("Polished the interface")
    assert "[memory decision · main-qa (duckterm-core) · recorded on " in answer
    assert "[memory decision · main (duckterm-core) · recorded on " in answer
    assert "interface" not in answer.split("Records:", 1)[1]
    assert "the newest wins" in answer
    assert "Searched for “touch” or “face”" in answer


def test_history_finds_agent_messages_and_survives_a_bad_rerank(fleet, monkeypatch):
    server, owner = fleet
    headers = enroll(server.history, "qa2", "duckterm-core")
    call(server.history, headers, "PATCH", "/self", {"activity": "QA"})
    with server.history._conn as conn:
        conn.execute(
            "INSERT INTO session_questions (id, sender, recipient, sender_name, root, question, "
            "status, answer, created_at, expires_at, idempotency_key, content_hash, kind) "
            "VALUES ('q1', 'agent', 'qa2', 'release-dev', 'duckterm-core', 'QA #500 please', "
            "'answered', '#500 PASS within isolated scope', ?, 0, 'k', 'h', 'question')",
            (int(time.time() * 1000),),
        )
    intent = {"intent": "ask.history", "entities": [topic("#500 QA", "500")], "text": None}
    answers = model(monkeypatch, intent, keep="not json")
    status, _ = ask(server, owner, "What did QA say about #500?")
    assert status == 200
    answer = answers[-1]
    assert "[agent message · release-dev (duckterm-leadership)" in answer
    assert "answer from main-qa-2: #500 PASS within isolated scope" in answer


def test_probe_sessions_and_test_events_never_reach_a_history_answer(fleet, monkeypatch):
    server, owner = fleet
    history = server.history
    now = int(time.time() * 1000)
    with history._conn as conn:
        conn.execute("UPDATE sessions SET test = 1 WHERE session_key = 'qa2'")
    chunk(history, "peer", "Real: shipped bug reporting.", now - DAY)
    chunk(history, "qa2", "Probe: bug reporting summary.", now - DAY)
    memory(history, "qa2", [{"id": "m", "kind": "decision", "text": "Probe bug reporting"}], now)
    history.folder_tasks.start("duckterm-core", "qa2", "Probe bug reporting task")
    history._conn.commit()
    with history._conn as conn:
        conn.execute(
            "INSERT INTO session_questions (id, sender, recipient, sender_name, root, question, "
            "status, created_at, expires_at, idempotency_key, content_hash, kind) "
            "VALUES ('q1', 'peer', 'qa2', 'main', 'duckterm-core', 'Probe bug reporting ask', "
            "'queued', ?, 0, 'k', 'h', 'question')",
            (now,),
        )
    history.record(
        {
            "_id": "probe-prompt",
            "_ts": now - 1000,
            "event_type": "UserPromptSubmit",
            "session_key": "qa2",
            "prompt": "Probe bug reporting prompt",
            "test": True,
        }
    )
    intent = {"intent": "ask.history", "entities": [topic("bug reporting")], "text": None}
    answers = model(monkeypatch, intent, keep="not json")
    status, _ = ask(server, owner, "Who built bug reporting?")
    assert status == 200
    records = answers[-1].split("Records:", 1)[1]
    assert "Real: shipped bug reporting." in records
    assert "Probe" not in records
    assert "qa2" not in {a["id"] for a in ask_intents.roster(server)}


def test_legacy_digests_are_read_only_for_spans_before_they_froze(fleet, monkeypatch):
    server, owner = fleet
    with server.history._conn as conn:
        conn.execute(
            "INSERT INTO digest_items (id, session_key, bucket, text, created_at, updated_at) "
            "VALUES ('d1', 'peer', 'deliverables', 'Bug reporting UI shipped in v0.4.113', ?, ?)",
            (1791000000000, 1791000000000),
        )
    entities = [topic("bug reporting")]
    answers = model(
        monkeypatch, {"intent": "ask.history", "entities": entities, "text": None}, keep=[0]
    )
    ask(server, owner, "Who built bug reporting?")
    assert "[legacy deliverables · main (duckterm-core)" in answers[-1]
    later = {
        "type": "time",
        "said": "this week",
        "from": "2026-11-23T00:00:00-08:00",
        "to": "2026-11-28T00:00:00-08:00",
    }
    answers = model(
        monkeypatch,
        {"intent": "ask.history", "entities": [*entities, later], "text": None},
        keep=[0],
    )
    ask(server, owner, "Who built bug reporting this week?")
    assert "legacy deliverables" not in answers[-1]


def test_merges_and_releases_name_the_agent_whose_branch_merged(fleet, monkeypatch, tmp_path):
    server, owner = fleet
    repo = tmp_path / "repo"

    def git(*args):
        subprocess.run(
            ["git", "-c", "tag.gpgsign=false", "-c", "commit.gpgsign=false", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    repo.mkdir()
    git("init", "-q", "-b", "main")
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "start")
    git("checkout", "-q", "-b", "feat-x")
    git(
        "-c",
        "user.email=a@b",
        "-c",
        "user.name=a",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Add bug reporting",
    )
    git("checkout", "-q", "main")
    git(
        "-c",
        "user.email=a@b",
        "-c",
        "user.name=a",
        "merge",
        "-q",
        "--no-ff",
        "feat-x",
        "-m",
        "Merge pull request #12 from u/feat-x",
        "-m",
        "Bug reporting",
    )
    git("tag", "v1.0")
    with server.history._conn as conn:
        conn.execute(
            "UPDATE sessions SET cwd = ?, branch = 'feat-x', updated_at = ? "
            "WHERE session_key = 'qa1'",
            (str(repo), int(time.time() * 1000)),
        )
    answers = model(
        monkeypatch,
        {"intent": "ask.history", "entities": [topic("bug reporting")], "text": None},
        keep=[],
    )
    ask(server, owner, "Who shipped bug reporting?")
    answer = answers[-1]
    assert "- merged #12 Bug reporting in repo by main-qa (duckterm-core, claude-code)" in answer
    assert "- release v1.0 in repo (1 merged PR)" in answer
    assert "- commit in repo, " in answer and ": Add bug reporting" in answer


def test_a_wide_question_is_summarised_per_workspace_first(fleet, monkeypatch):
    server, owner = fleet
    now = int(time.time() * 1000)
    chunk(server.history, "peer", "Core work " + "x" * 200, now - DAY)
    chunk(server.history, "main-b", "Bodh work " + "y" * 200, now - DAY)
    monkeypatch.setattr("duckterm.core.ask_intents.WIDE_BYTES", 100)
    answers = model(monkeypatch, {"intent": "ask.history", "entities": [], "text": None})
    ask(server, owner, "What did the fleet do this week?")
    passes = [a for a in answers if a.startswith("Summarise what agents in the workspace")]
    assert sorted(p.split("'")[1] for p in passes) == [
        "BodhBytes",
        "duckterm-core",
        "duckterm-leadership",
    ]
    assert "Workspace duckterm-core (summarised first):\nThe answer." in answers[-1]
    assert "too many to read at once" in answers[-1]


def test_a_wide_question_reads_only_the_largest_workspaces_and_newest_chunks(fleet, monkeypatch):
    server, owner = fleet
    now = int(time.time() * 1000)
    chunk(server.history, "peer", "Old core " + "o" * 300, now - 3 * DAY)
    chunk(server.history, "peer", "New core " + "n" * 300, now - DAY)
    chunk(server.history, "main-b", "Bodh " + "y" * 50, now - DAY)
    monkeypatch.setattr("duckterm.core.ask_intents.WIDE_BYTES", 100)
    monkeypatch.setattr("duckterm.core.ask_intents.WIDE_PASSES", 2)
    monkeypatch.setattr("duckterm.core.ask_intents.WIDE_PASS_BYTES", 400)
    answers = model(monkeypatch, {"intent": "ask.history", "entities": [], "text": None})
    ask(server, owner, "What did the fleet do this week?")
    passes = {
        a.split("'")[1]: a
        for a in answers
        if a.startswith("Summarise what agents in the workspace")
    }
    assert len(passes) == 2 and "duckterm-core" in passes
    assert "New core" in passes["duckterm-core"] and "Old core" not in passes["duckterm-core"]
    (left,) = {"BodhBytes", "duckterm-core", "duckterm-leadership"} - set(passes)
    assert f"say that these were not: {left}." in answers[-1]


def test_a_topic_without_an_agent_is_a_history_search(fleet, monkeypatch):
    server, owner = fleet
    intent = {"intent": "ask.agent", "entities": [topic("bug reporting")], "text": None}
    model(monkeypatch, intent)
    status, body = ask(server, owner, "Which agent worked on bug reporting?")
    assert status == 200 and body["intent"] == "ask.history"


def test_sent_lists_terminal_prompts_with_who_sent_them(fleet, monkeypatch):
    server, owner = fleet
    history = server.history
    now = int(time.time() * 1000)
    for i, (kind, field, text) in enumerate(
        [
            ("PromptDelivery", "text", "Duckterm Oracle: you have 1 inbox item"),
            ("UserPromptSubmit", "prompt", "Duckterm Oracle: you have 1 inbox item"),
            ("UserPromptSubmit", "prompt", "merge 525 on pass"),
            ("UserPromptSubmit", "prompt", "<task-notification>\n<task-id>b1</task-id>"),
        ]
    ):
        history.record(
            {
                "_id": f"p{i}",
                "_ts": now - 1000 + i,
                "event_type": kind,
                "session_key": "agent",
                field: text,
            }
        )
    intent = {
        "intent": "ask.sent",
        "entities": [agent("release-dev", ["agent"], 0.5)],
        "text": None,
    }
    answer = listed(server, owner, monkeypatch, intent, "What did I ask release-dev?")
    prompts = answer.split("Prompts the agents received in their terminals, newest first:\n", 1)[1]

    def when(ms):
        return time.strftime("%b %-d %H:%M", time.localtime(ms / 1000))

    who = "- to **release-dev** (duckterm-leadership)"
    lines = prompts.splitlines()
    pairs = dict(zip(lines[1::2], lines[0::2], strict=True))
    assert (
        pairs["  “merge 525 on pass”"]
        == f"{who} · {when(now - 998)} · typed or pasted in the terminal"
    )
    assert pairs["  “Duckterm Oracle: you have 1 inbox item”"] == (
        f"{who} · {when(now - 999)} · delivered by DuckTerm"
    )
    assert "task-notification" not in answer


# ── Step 4: pull requests from a background copy ──


def pr_row(number, status, label, needs_you, key, name, failed=0):
    return {
        "repo": "/work/duckterm",
        "branch": f"b{number}",
        "pr": {"number": number, "title": f"Change {number}", "state": "open", "head_sha": "abc"},
        "status": status,
        "label": label,
        "needs_you": needs_you,
        "session": {"key": key, "name": name},
        "checks": {"total": 3, "failed": failed, "pending": 0, "passed": 3 - failed},
        "review": "none",
        "conflict": status == "conflict",
    }


@pytest.fixture
def github(monkeypatch):
    """review_lists.build reads git and GitHub; the copy around it is real."""
    builds = Builds()

    def build(members, *, owners=None, refresh=False, deadline=None):
        builds.append([m["session_key"] for m in members])
        time.sleep(builds.delay)
        if builds.error:
            raise builds.error
        return {
            "rows": [
                pr_row(531, "conflict", "Conflicts", True, "peer", "main"),
                pr_row(529, "ci_failing", "CI failing", True, "qa1", "main-qa", failed=2),
                pr_row(500, "ready", "Ready", True, "agent", "release-dev"),
                pr_row(532, "draft", "Draft", False, "qa2", "main-qa-2"),
            ],
            "repos": [
                {"name": "duckterm", "error": None},
                {"name": "bodhbit", "error": "Reconnect the GitHub connector"},
                {"name": "sotto", "error": "Reconnect the GitHub connector"},
            ],
        }

    monkeypatch.setattr("duckterm.review.lists.build", build)
    return builds


class Builds(list):
    delay = 0.0
    error: Exception | None = None


def test_prs_are_grouped_from_a_copy_built_once(fleet, monkeypatch, github):
    server, owner = fleet
    intent = {"intent": "ask.prs", "entities": []}
    first = listed(server, owner, monkeypatch, intent, "Which PRs need me?")
    lines = first.splitlines()
    assert lines[0].startswith("3 of 4 open pull requests need you, as of ")
    assert (
        "**Merge conflicts** (1)\n- #531 Change 531 · Conflicts · **main** (duckterm-core)" in first
    )
    assert (
        "- #529 Change 529 · CI failing · **main-qa** (duckterm-core) · 2 check(s) failing" in first
    )
    assert (
        "**Ready for you** (1)\n- #500 Change 500 · Ready · **release-dev** (duckterm-leadership)"
        in first
    )
    assert "1 more are drafts or have changes requested (not listed)." in first
    assert "Couldn't read GitHub for bodhbit, sotto: Reconnect the GitHub connector" in first
    listed(server, owner, monkeypatch, intent, "Which PRs need me?")
    assert len(github) == 1
    # A stale copy answers at once and a newer one is built behind it.
    at, result = server._pr_list_copy
    server._pr_list_copy = (at - 6 * 60_000, result)
    stale = listed(server, owner, monkeypatch, intent, "Which PRs need me?")
    assert "A newer copy is being fetched." in stale

    # Each test request runs its own event loop, which cancels the rebuild it
    # started when it closes; the server's loop doesn't. Wait for one here.
    async def rebuilt():
        server._pr_list_copy = (at - 6 * 60_000, result)
        assert (await server._pr_copy())[2]
        await server._pr_list_refresh

    asyncio.run(rebuilt())
    assert len(github) >= 2 and server._pr_list_copy[0] >= at


def test_the_pr_list_reads_recent_agents_and_shows_their_text_inert(fleet, github):
    server, _ = fleet
    now = int(time.time() * 1000)
    with server.history._conn as conn:
        conn.execute(
            "UPDATE sessions SET state = 'stopped', updated_at = ? WHERE session_key = 'main-b'",
            (now - 10 * DAY,),
        )
    asyncio.run(server._pr_copy())
    assert "main-b" not in github[0] and "peer" in github[0]
    row = pr_row(7, "ready", "Ready", True, "peer", "main")
    row["pr"]["title"] = "Fix ![p](https://evil.example/p.png) [docs](https://evil.example)"
    copy = {
        "rows": [row],
        "repos": [{"name": "sotto", "error": "fatal: bad token\nAuthorization: Bearer ghp_x"}],
    }
    answer = ask_intents.prs_list(copy, now, {"peer": ("main", "duckterm-core")}, False)
    assert "Fix \\!\\[p\\](https://evil.example/p.png) \\[docs\\](https://evil.example)" in answer
    assert answer.endswith("Couldn't read GitHub for sotto: fatal: bad token")


def test_concurrent_cold_questions_share_one_build(fleet, github):
    server, _ = fleet
    github.delay = 0.2

    async def six():
        return await asyncio.gather(*(server._pr_copy() for _ in range(6)))

    copies = asyncio.run(six())
    assert len(github) == 1
    assert all(c[0] is copies[0][0] and c[0]["rows"] for c in copies)


def test_a_slow_cold_copy_answers_not_ready_and_keeps_its_one_build(fleet, monkeypatch, github):
    server, owner = fleet
    github.delay = 0.3
    monkeypatch.setattr("duckterm.core.ask_intents.PR_WAIT_S", 0.05)

    async def question_then_wait():
        first = await server._pr_copy()
        await server._pr_list_refresh
        return first, await server._pr_copy()

    (copy, _, refreshing), (later, _, _) = asyncio.run(question_then_wait())
    assert copy is None and refreshing
    assert len(github) == 1 and later["rows"]
    server._pr_list_copy = None
    server._pr_list_refresh = None
    github.delay = 0.3
    answer = listed(server, owner, monkeypatch, {"intent": "ask.prs", "entities": []}, "PRs?")
    assert answer == ask_intents.PR_NOT_READY


def test_a_failed_build_is_answered_and_not_retried_per_question(fleet, monkeypatch, github):
    server, owner = fleet
    github.error = RuntimeError("git: not a repository")
    intent = {"intent": "ask.prs", "entities": []}
    answer = listed(server, owner, monkeypatch, intent, "Which PRs need me?")
    assert answer.startswith("Couldn't build the PR list at ")
    assert "RuntimeError: git: not a repository. It's tried again 5 minutes after that." in answer
    listed(server, owner, monkeypatch, intent, "Which PRs need me?")
    assert len(github) == 1
    health = listed(server, owner, monkeypatch, {"intent": "ask.health", "entities": []}, "?")
    assert "- Pull requests: couldn't build the PR list at " in health


def test_the_pr_list_build_stops_reading_repositories_at_its_deadline(tmp_path):
    from duckterm.review import lists as review_lists

    repo = tmp_path / "late"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feat", str(repo)], check=True)
    row = {"session_key": "k", "name": "dev", "cwd": str(repo)}
    result = review_lists.build([row], deadline=time.monotonic() - 1)
    assert result["rows"] == []
    assert [(r["name"], r["error"]) for r in result["repos"]] == [
        ("late", "the PR list ran out of time before reaching it")
    ]


def test_a_pr_question_reads_its_row_and_what_agents_said(fleet, monkeypatch, github):
    server, owner = fleet
    with server.history._conn as conn:
        conn.execute(
            "INSERT INTO session_questions (id, sender, recipient, sender_name, root, question, "
            "status, answer, created_at, expires_at, idempotency_key, content_hash, kind) "
            "VALUES ('q1', 'agent', 'qa2', 'release-dev', 'duckterm-core', 'QA #500 please', "
            "'answered', 'PASS within isolated scope', ?, 0, 'k', 'h', 'question')",
            (int(time.time() * 1000),),
        )
    pr = {"type": "pr", "said": "#500", "id": None, "confidence": 0.95}
    intent = {"intent": "ask.pr", "entities": [pr, agent("QA", ["qa1", "qa2"], 0.6)], "text": None}
    answers = model(monkeypatch, intent, keep=[0, 1])
    status, body = ask(server, owner, "What did QA say about #500?")
    assert status == 200 and body["intent"] == "ask.pr"
    answer = answers[-1]
    assert "Pull request:\nPR #500 Change 500 (as of " in answer
    assert "): Ready; branch b500 by release-dev;" in answer
    assert "[agent message · release-dev (duckterm-leadership)" in answer
    assert "answer from main-qa-2: PASS within isolated scope" in answer


# ── Step 5: actions are plans the owner confirms ──


def act(server, owner, monkeypatch, intent, question):
    answers = model(monkeypatch, {"text": None, **intent})
    status, body = ask(server, owner, question, request_key="k-act")
    assert status == 200, body
    return body, answers


def test_stop_needs_one_certain_agent_and_shows_what_it_is_doing(fleet, monkeypatch):
    server, owner = fleet
    with server.history._conn as conn:
        conn.execute(
            "UPDATE sessions SET state = 'busy', updated_at = ? WHERE session_key = 'agent'",
            (int(time.time() * 1000) - 4 * 60_000,),
        )
    body, _ = act(
        server,
        owner,
        monkeypatch,
        {"intent": "act.stop", "entities": [agent("release-dev", ["agent"], 0.95)]},
        "Stop release-dev",
    )
    assert body == {
        "action": {
            "kind": "stop",
            "agent": {"id": "agent", "name": "release-dev", "folder": "duckterm-leadership"},
            "doing": "busy · Merging #496, then #485 · for 4 min",
        }
    }
    # Two plausible agents, or a sure one under 0.9, ask which; nothing is planned.
    for ids, confidence in ((["peer", "main-b"], 0.95), (["peer"], 0.8)):
        body, _ = act(
            server,
            owner,
            monkeypatch,
            {"intent": "act.stop", "entities": [agent("main", ids, confidence)]},
            "Stop main",
        )
        assert "action" not in body and body["answer"].endswith("Nothing was done.")
    assert server.history.session("agent")["state"] == "busy"


def test_answer_must_match_an_open_question_from_that_agent(fleet, monkeypatch):
    server, owner = fleet
    body, _ = act(
        server,
        owner,
        monkeypatch,
        {"intent": "act.answer", "entities": [agent("main-qa", ["qa1"], 0.95)], "text": "yes"},
        "Tell main-qa yes",
    )
    note = body["action"]["note"]
    assert body["action"]["kind"] == "answer" and body["action"]["text"] == "yes"
    assert note["question"] == "Approve QA on Ducky PRs outside the batch?"
    assert server.relay.get(note["id"])["status"] == "open"
    # main-qa-2's only note is an offer, which doesn't wait on the owner.
    body, _ = act(
        server,
        owner,
        monkeypatch,
        {"intent": "act.answer", "entities": [agent("main-qa-2", ["qa2"], 0.95)], "text": "yes"},
        "Tell main-qa-2 yes",
    )
    assert (
        body["answer"]
        == "main-qa-2 (duckterm-core) has no open question for you. Nothing was sent."
    )


def test_approve_opens_the_approve_screen_for_an_open_pr_only(fleet, monkeypatch, github):
    server, owner = fleet
    pr = {"type": "pr", "said": "#500", "id": None, "confidence": 0.99}
    body, _ = act(
        server, owner, monkeypatch, {"intent": "act.approve", "entities": [pr]}, "Merge #500"
    )
    assert body["action"]["kind"] == "approve"
    assert body["action"]["pr"] == {
        "repo": "/work/duckterm",
        "number": 500,
        "title": "Change 500",
        "label": "Ready",
        "head_sha": "abc",
    }
    missing = {**pr, "said": "#999"}
    body, _ = act(
        server, owner, monkeypatch, {"intent": "act.approve", "entities": [missing]}, "Merge #999"
    )
    assert body["answer"].startswith("#999 isn't an open pull request in the PR list")
    # A cold copy that isn't ready in time offers no card.
    server._pr_list_copy = None
    github.delay = 0.3
    monkeypatch.setattr("duckterm.core.ask_intents.PR_WAIT_S", 0.05)
    body, _ = act(server, owner, monkeypatch, {"intent": "act.approve", "entities": [pr]}, "Merge")
    assert body["answer"] == ask_intents.PR_NOT_READY and "action" not in body


def test_a_question_for_an_agent_shows_what_the_records_already_say(fleet, monkeypatch):
    server, owner = fleet
    chunk(server.history, "agent", "Fleet chat fix #536 merged.", int(time.time() * 1000))
    intent = {
        "intent": "act.message",
        "entities": [agent("release-dev", ["agent"], 0.95), topic("fleet chat fix", "#536")],
        "text": "Is the fleet chat fix done?",
    }
    answers = model(monkeypatch, intent)
    status, body = ask(
        server, owner, "Ask release-dev if the fleet chat fix is done", request_key="k"
    )
    assert status == 200
    assert body["plan"]["records"] == "The answer."
    assert "Fleet chat fix #536 merged." in answers[-1]
    assert answers[-1].startswith("The owner is about to ask an agent the question below.")
    # A statement isn't checked against records.
    model(monkeypatch, {**intent, "text": "rebase #446"})
    status, body = ask(server, owner, "Tell release-dev to rebase #446", request_key="k2")
    assert "records" not in body["plan"]


def test_actions_need_oracle_messages(fleet, monkeypatch):
    server, owner = fleet
    server.edition = EditionConfig(
        server.edition.name, server.edition.features - {"oracle_messages"}
    )
    answers = model(
        monkeypatch,
        {"intent": "act.stop", "entities": [agent("release-dev", ["agent"], 0.95)], "text": None},
    )
    status, body = ask(server, owner, "Stop release-dev", request_key="k")
    assert status == 200 and "action" not in body
    assert answers[-1].startswith("You oversee a fleet")
