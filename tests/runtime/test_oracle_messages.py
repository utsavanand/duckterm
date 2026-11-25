"""Ask Oracle telling one agent something: the owner's own words go straight to
its inbox, reworded text and unclear recipients come back for confirmation, and
the model can never pick a session it wasn't shown."""

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


def test_owners_own_words_to_one_agent_are_sent_and_logged(messaging, monkeypatch) -> None:
    server, owner, history = messaging
    route(monkeypatch, {"action": "message", "session_id": "pm", "text": "rebase #446"})
    status, body = ask(server, owner, "Tell product-manager to rebase #446")
    assert status == 200 and body["sessions"] == ["pm"]
    assert "Sent to **product-manager**" in body["answer"]
    assert mail(history) == ["rebase #446"]
    # A retried chat turn reuses its key and doesn't send twice.
    ask(server, owner, "Tell product-manager to rebase #446")
    assert mail(history) == ["rebase #446"]


def test_reworded_text_is_a_plan_and_nothing_is_sent(messaging, monkeypatch) -> None:
    server, owner, history = messaging
    route(monkeypatch, {"action": "message", "session_id": "pm", "text": "Please rebase PR 446."})
    status, body = ask(server, owner, "get product-manager to rebase 446")
    assert status == 200
    assert body["plan"] == {
        "id": "pm",
        "name": "product-manager",
        "folder": "Duckterm",
        "text": "Please rebase PR 446.",
    }
    assert mail(history) == []


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
