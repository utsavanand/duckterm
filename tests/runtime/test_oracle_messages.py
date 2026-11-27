"""Ask Oracle telling one agent something: whatever the model routes comes back
as a plan or candidates for the owner to confirm, the model can never pick a
session it wasn't shown, and the confirmed send is saved to the chat once."""

import json

import pytest
from tests.runtime.test_relay import post, world  # noqa: F401  (fixture)
from tests.runtime.test_session_api import dispatch

from duckterm.editions import EditionConfig
from duckterm.llm.summarizer import Summary


def route(monkeypatch, plan):
    prompts = []

    def summarize(prompt, **_kw):
        prompts.append(prompt)
        return Summary(text=json.dumps(plan), backend="cli")

    monkeypatch.setattr("duckterm.server.summarize", summarize)
    return prompts


def ask(server, owner, question, key="k1"):
    return post(server, owner, "/fleet/ask", {"question": question, "request_key": key})


def mail(history):
    return [m["question"] for m in history.session_api.open_mail("pm")]


@pytest.fixture
def messaging(world):  # noqa: F811
    server, owner, sup, history = world
    features = server.edition.features | {"oracle_messages"}
    server.edition = EditionConfig(server.edition.name, features)
    return server, owner, history


@pytest.mark.parametrize(
    ("question", "plan"),
    [
        # The owner's own words to the agent they named.
        ("Tell product-manager to rebase #446", ("pm", "rebase #446")),
        # Reworded text.
        ("get product-manager to rebase 446", ("pm", "Please rebase PR 446.")),
        # The model picked an agent the owner didn't name (security review of #474).
        ("Tell main-dev to rebase #446", ("pm", "rebase #446")),
        # A negated question read as a send.
        ("Should I tell product-manager not to merge yet?", ("pm", "merge")),
    ],
)
def test_a_routed_message_is_a_plan_and_nothing_is_sent(
    messaging, monkeypatch, question, plan
) -> None:
    server, owner, history = messaging
    session_id, text = plan
    route(monkeypatch, {"action": "message", "session_id": session_id, "text": text})
    status, body = ask(server, owner, question)
    assert status == 200
    assert body == {
        "plan": {"id": "pm", "name": "product-manager", "folder": "Duckterm", "text": text}
    }
    assert mail(history) == []
    assert dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"] == []


def test_unclear_recipient_returns_candidates_and_sends_nothing(messaging, monkeypatch) -> None:
    server, owner, history = messaging
    route(monkeypatch, {"action": "clarify", "candidates": ["pm"], "text": "rebase #446"})
    status, body = ask(server, owner, "tell the PM to rebase #446")
    assert status == 200
    assert body["clarify"]["candidates"] == [
        {"id": "pm", "name": "product-manager", "folder": "Duckterm"}
    ]
    assert mail(history) == []


@pytest.mark.parametrize(
    "plan",
    [
        {"action": "message", "session_id": "not-shown", "text": "rebase"},
        {"action": "clarify", "candidates": ["not-shown"], "text": ""},
        {"action": "message", "session_id": "pm", "text": "rebase", "priority": True},
    ],
)
def test_a_plan_outside_what_the_model_was_shown_is_refused(messaging, monkeypatch, plan) -> None:
    server, owner, history = messaging
    route(monkeypatch, plan)
    status, body = ask(server, owner, "tell product-manager to rebase")
    assert status == 502 and "Nothing was sent" in body["error"]
    assert mail(history) == []


def test_without_the_flag_every_message_is_a_question(world, monkeypatch) -> None:  # noqa: F811
    server, owner, sup, history = world
    monkeypatch.setattr(sup, "screen_text", lambda lines: "", raising=False)
    prompts = route(monkeypatch, {"action": "message", "session_id": "pm", "text": "rebase"})
    status, body = ask(server, owner, "Tell product-manager to rebase")
    assert status == 200 and "plan" not in body and "clarify" not in body
    assert mail(history) == [] and "Classify the owner's latest message" not in prompts[0]


@pytest.mark.parametrize("flag", [True, False])
def test_an_ask_oracle_send_is_saved_in_the_chat_history(world, flag) -> None:  # noqa: F811
    server, owner, _, history = world
    if flag:
        features = server.edition.features | {"oracle_messages"}
        server.edition = EditionConfig(server.edition.name, features)
    body = {"text": "rebase #446", "mode": "inbox", "oracle_chat": "@product-manager rebase #446"}
    status, reply = post(server, owner, "/sessions/pm/message", body)
    assert status == 200 and mail(history) == ["rebase #446"]
    chat = dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"]
    if flag:
        assert reply["exchange"]["q"] == "@product-manager rebase #446"
        assert [m["a"] for m in chat] == [
            "Sent to **product-manager**: \u201crebase #446\u201d. It's in its inbox."
        ]
    else:
        assert "exchange" not in reply and chat == []


def test_a_retried_send_is_delivered_and_saved_once(messaging) -> None:
    server, owner, history = messaging
    body = {
        "text": "rebase #446",
        "mode": "inbox",
        "request_key": "oracle:k1",
        "oracle_chat": "@product-manager rebase #446",
    }
    first = post(server, owner, "/sessions/pm/message", body)[1]
    # The response was lost and the owner pressed Send again.
    second = post(server, owner, "/sessions/pm/message", body)[1]
    assert mail(history) == ["rebase #446"]
    assert second["exchange"] == first["exchange"]
    chat = dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"]
    assert [m["q"] for m in chat] == ["@product-manager rebase #446"]


def test_the_recipients_answer_comes_back_into_the_chat_and_outlives_its_mail(messaging) -> None:
    from tests.runtime.test_session_api import call, token_for

    server, owner, history = messaging
    body = {
        "text": "status of #446?",
        "mode": "inbox",
        "oracle_chat": "@product-manager status of #446?",
    }
    sent = post(server, owner, "/sessions/pm/message", body)[1]
    assert sent["exchange"]["to"] == "product-manager"
    [waiting] = dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"]
    assert waiting["reply"]["status"] == "queued" and waiting["reply"]["answer"] is None

    path = f"/questions/{sent['message_id']}"
    answer = {"text": "Rebased, CI green."}
    call(history, token_for(history, "pm"), "POST", path + "/answer", answer)
    [answered] = dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"]
    assert answered["reply"]["status"] == "answered"
    assert answered["reply"]["answer"] == "Rebased, CI green."

    # The broker retires old mail; the saved reply stays.
    with history._conn:
        history._conn.execute("DELETE FROM session_questions")
    [kept] = dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"]
    assert kept["reply"]["answer"] == "Rebased, CI green."


def test_a_long_answer_is_saved_cut_short(messaging) -> None:
    # #501 security review: 200 saved answers of up to 256 KB each.
    from tests.runtime.test_session_api import call, token_for

    from duckterm.core import oracle

    server, owner, history = messaging
    body = {"text": "full log?", "mode": "inbox", "oracle_chat": "@product-manager full log?"}
    sent = post(server, owner, "/sessions/pm/message", body)[1]
    long = "x" * 100_000
    path = f"/questions/{sent['message_id']}/answer"
    call(history, token_for(history, "pm"), "POST", path, {"text": long})
    [saved] = dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"]
    assert saved["reply"]["answer"] == long[: oracle.REPLY_LIMIT] + oracle.REPLY_CUT
    # Cut once: the next read finds it settled and leaves it alone.
    [again] = dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"]
    assert again["reply"] == saved["reply"]


@pytest.mark.parametrize("state", ["terminated", "stopped", "interrupted", "archived", "merged"])
def test_home_chat_refuses_a_session_that_has_ended(messaging, state) -> None:
    server, owner, history = messaging
    with history._conn:
        history._conn.execute("UPDATE sessions SET state = ? WHERE session_key = 'pm'", (state,))
    body = {"text": "status?", "mode": "inbox", "oracle_chat": "@product-manager status?"}
    status, reply = post(server, owner, "/sessions/pm/message", body)
    assert (status, reply) == (409, {"error": "product-manager has ended. Nothing was sent."})
    assert mail(history) == []
    assert dispatch(server, "GET", "/oracle/chat", owner)[1]["messages"] == []
    # A plain inbox message, not from Home's chat, is unchanged.
    status, sent = post(server, owner, "/sessions/pm/message", {"text": "fyi", "mode": "inbox"})
    assert status == 200 and sent["delivered"] == "inbox"
