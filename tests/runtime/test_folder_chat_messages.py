"""Natural-language folder sends use the real broker, scope and saved replies."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from tests.runtime.test_folder_view import app, req  # noqa: F401
from tests.runtime.test_session_api import call, enroll


def plan(kind="session", target="one", text="What are you working on?"):
    return {"action": "message", "target": {"kind": kind, "id": target}, "text": text}


def ask(server, question="Ask Session one what they are working on", key="first", **kwargs):
    return req(
        server,
        "POST",
        "/fleet/ask",
        {
            "folder": "a",
            "question": question,
            "request_key": key,
        },
        **kwargs,
    )


def model(monkeypatch, output):
    prompts = []

    def summarize(prompt):
        prompts.append(prompt)
        return SimpleNamespace(text=json.dumps(output), backend="test")

    monkeypatch.setattr("duckterm.server.summarize", summarize)
    return prompts


def test_direct_message_retries_and_saved_reply(app, monkeypatch):  # noqa: F811
    credentials = enroll(app.history, "one", "a")
    prompts = model(monkeypatch, plan())
    app.history.set_intention("one", "UNTRUSTED_TERMINAL_INSTRUCTIONS")
    status, result = ask(app)
    assert status == 200
    exchange = result["exchange"]
    assert exchange["q"] == "Ask Session one what they are working on"
    assert exchange["dispatch"]["text"] == "What are you working on?"
    assert result["sessions"] == ["one"]
    assert "UNTRUSTED_TERMINAL_INSTRUCTIONS" not in prompts[0]
    assert "Session outside" not in prompts[0]
    inbox = call(app.history, credentials, "GET", "/inbox")[1]["messages"]
    assert len(inbox) == 1
    assert inbox[0]["sender_kind"] == "owner"
    assert inbox[0]["question"] == "What are you working on?"
    assert ask(app)[1] == result
    assert len(prompts) == 1  # A lost response never reinterprets the request.
    assert ask(app, "Send something different")[0] == 409
    mid = exchange["dispatch"]["recipients"][0]["message_id"]
    assert (
        call(app.history, credentials, "POST", f"/questions/{mid}/answer", {"text": "Testing"})[0]
        == 200
    )
    saved = req(app, "GET", "/folders/a/chat")[1]["messages"]
    assert saved[0]["dispatch"]["recipients"][0]["answer"] == "Testing"


@pytest.mark.parametrize("target, expected", [("a", {"one", "two"}), ("a/child", {"two"})])
def test_broadcast_to_current_folder_or_subfolder(app, monkeypatch, target, expected):  # noqa: F811
    for key in ("one", "two", "outside"):
        enroll(app.history, key, "a" if key != "outside" else "ab")
    model(monkeypatch, plan("folder", target, "Report your progress"))
    status, body = ask(app, "Tell everyone in this folder to report progress")
    assert status == 200
    assert set(body["sessions"]) == expected
    assert set(app.history.session_api.pending_counts()) == expected
    assert ask(app, "Tell everyone in this folder to report progress")[1] == body


@pytest.mark.parametrize(
    "output, status",
    [
        (plan("session", "outside"), 409),
        (plan("folder", "ab"), 409),
        (plan("folder", "a/unknown"), 409),
        ({"action": "shell", "command": "echo bad"}, 502),
        ({**plan(), "assign": True}, 502),
        ([], 502),
        (None, 502),
    ],
)
def test_invalid_plans_never_enqueue(app, monkeypatch, output, status):  # noqa: F811
    enroll(app.history, "one", "a")
    enroll(app.history, "outside", "ab")
    model(monkeypatch, output)
    assert ask(app)[0] == status
    assert app.history.session_api.pending_counts() == {}


def test_no_silent_partial_broadcast_or_unenrolled_direct_send(app, monkeypatch):  # noqa: F811
    enroll(app.history, "one", "a")
    app.history._conn.execute("DELETE FROM session_api_members WHERE session_key = 'two'")
    app.history._conn.commit()
    model(monkeypatch, plan("folder", "a"))
    assert ask(app)[0] == 409
    assert app.history.session_api.pending_counts() == {}
    model(monkeypatch, plan("session", "two"))
    assert ask(app)[0] == 409


def test_clarification_and_normal_question_do_not_send(app, monkeypatch):  # noqa: F811
    enroll(app.history, "one", "a")
    model(monkeypatch, {"action": "clarify"})
    status, body = ask(app, "Tell them")
    assert status == 200 and "Nothing was sent" in body["answer"]
    outputs = iter(['{"action":"answer"}', "Session one is testing."])
    monkeypatch.setattr(
        "duckterm.server.summarize", lambda _: SimpleNamespace(text=next(outputs), backend="test")
    )
    status, body = ask(app, "What are the agents doing?")
    assert status == 200 and body["answer"] == "Session one is testing."
    assert app.history.session_api.pending_counts() == {}


def test_auth_and_invalid_retry_keys_do_not_invoke_model(app, monkeypatch):  # noqa: F811
    prompts = model(monkeypatch, plan())
    assert ask(app, headers={})[0] == 401
    assert ask(app, headers=enroll(app.history, "one", "a"))[0] == 403
    for key in ("", None, [], "x" * 60, "a:b"):
        assert ask(app, key=key)[0] == 400
    assert prompts == []


@pytest.mark.parametrize("text", ["", "Message sent!", '{"action":'])
def test_bad_model_response_cannot_claim_delivery(app, monkeypatch, text):  # noqa: F811
    monkeypatch.setattr(
        "duckterm.server.summarize", lambda _: SimpleNamespace(text=text, backend="test")
    )
    assert ask(app)[0] == 502
    assert app.history.session_api.pending_counts() == {}
    assert req(app, "GET", "/folders/a/chat")[1]["messages"] == []


def test_empty_folder_needs_no_model_and_stopped_agents_can_receive(app, monkeypatch):  # noqa: F811
    prompts = model(monkeypatch, plan())
    app.history.create_folder("empty")
    assert (
        req(
            app,
            "POST",
            "/fleet/ask",
            {
                "folder": "empty",
                "question": "Tell everyone to report progress",
                "request_key": "empty",
            },
        )[1]["answer"]
        == "No sessions are running in empty."
    )
    assert prompts == []
    enroll(app.history, "one", "a")
    app.history.set_state("one", "stopped")
    assert ask(app)[0] == 200
    assert app.history.session_api.pending_counts() == {"one": 1}


@pytest.mark.parametrize("change", ["move", "rename", "unenroll", "state"])
def test_changes_during_inference(app, monkeypatch, change):  # noqa: F811
    enroll(app.history, "one", "a")
    original = app._fleet_ask
    loop = None

    async def handler(writer, body):
        nonlocal loop
        loop = asyncio.get_running_loop()
        await original(writer, body)

    async def mutate():
        if change == "move":
            app.history.set_meta("one", group="ab")
        elif change == "rename":
            app.history.folder_chats.change("a", "new", lambda: app.history.move_folder("a", "new"))
        elif change == "unenroll":
            app.history._conn.execute("DELETE FROM session_api_members WHERE session_key = 'one'")
            app.history._conn.commit()
        else:
            app.history.set_state("one", "idle")

    def summarize(_):
        asyncio.run_coroutine_threadsafe(mutate(), loop).result(timeout=5)
        return SimpleNamespace(text=json.dumps(plan()), backend="test")

    monkeypatch.setattr(app, "_fleet_ask", handler)
    monkeypatch.setattr("duckterm.server.summarize", summarize)
    assert ask(app)[0] == (200 if change == "state" else 409)
    if change != "state":
        assert app.history.session_api.pending_counts() == {}


def test_history_failure_and_changed_plan_cannot_duplicate_send(app, monkeypatch):  # noqa: F811
    enroll(app.history, "one", "a")
    enroll(app.history, "two", "a")
    model(monkeypatch, plan())
    append = app.history.folder_chats.append
    monkeypatch.setattr(
        app.history.folder_chats,
        "append",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("write failed")),
    )
    assert ask(app)[0] == 409
    monkeypatch.setattr(app.history.folder_chats, "append", append)
    model(monkeypatch, plan("folder", "a"))
    assert ask(app)[0] == 409
    assert app.history.session_api.pending_counts() == {"one": 1}
    model(monkeypatch, plan())
    assert ask(app)[0] == 200
    assert app.history.session_api.pending_counts() == {"one": 1}
