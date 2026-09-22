import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from duckterm.agents.inbox_prompt import Prompt, empty_prompt
from duckterm.cli import build_parser
from duckterm.core.inbox_delivery import REMINDER
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    history = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=history)
    tokens = {}
    for key in ("sender", "recipient"):
        history.record(
            {"_id": key, "_ts": 1, "event_type": "SessionStart", "session_key": key, "test": True}
        )
        history.set_meta(key, group="team")
        token = history.session_api.enroll(key, {"root": "team"})["token"]
        tokens[key] = {"authorization": "Bearer " + token}
    history.set_state("recipient", "idle")
    sent = []
    supervisor = SimpleNamespace(
        running=True,
        _tmux_target="rd_recipient",
        _byte_subs=set(),
        _last_input=0,
        _last_output=0,
        _input_queue=None,
        runtime=SimpleNamespace(name="codex"),
        write_bytes=lambda data: sent.append(data) or True,
    )
    monkeypatch.setattr(server.orchestrator, "get", lambda key: supervisor)
    monkeypatch.setattr(
        "duckterm.core.inbox_delivery.inbox_prompt.probe",
        lambda *_: Prompt("codex", 2, 0, "stable"),
    )

    def call(who, method, path, body=None, idem="one"):
        return history.session_api.handle(
            method,
            "/api/v1/session" + path,
            {**tokens[who], "idempotency-key": idem},
            json.dumps(body or {}).encode(),
        )[1]

    def ask(idem="one", timeout=0):
        return call(
            "sender",
            "POST",
            "/questions",
            {
                "target_session_id": "recipient",
                "question": "Peer-controlled text MUST NOT be typed",
                "timeout_seconds": timeout,
            },
            idem,
        )

    yield history, server, supervisor, sent, call, ask
    history.close()


def test_default_persistent_acceptance_and_restart(scenario, monkeypatch, tmp_path):
    history, _, _, _, call, ask = scenario
    question = ask()
    assert question["expires_at"] == 0
    call("recipient", "POST", f"/questions/{question['id']}/accept")
    future = time.time() + 30 * 86400
    monkeypatch.setattr("duckterm.core.session_api.time.time", lambda: future)
    assert call("recipient", "GET", "/inbox")["messages"][0]["status"] == "accepted"
    reopened = HistoryStore(tmp_path / "db.sqlite")
    assert (
        reopened.session_api.inbox("recipient", owner=True)["messages"][0]["status"] == "accepted"
    )
    reopened.close()
    assert build_parser().parse_args(["session", "ask", "recipient", "task"]).timeout == 0


def test_explicit_deadline_still_expires_after_acceptance(scenario, monkeypatch):
    _, _, _, _, call, ask = scenario
    question = ask(timeout=30)
    call("recipient", "POST", f"/questions/{question['id']}/accept")
    monkeypatch.setattr(
        "duckterm.core.session_api.time.time", lambda: question["expires_at"] / 1000 + 1
    )
    assert call("recipient", "GET", "/inbox")["messages"][0]["status"] == "expired"


def test_idle_wake_coalesces_and_does_not_inject_peer_text(scenario):
    history, server, supervisor, sent, _, ask = scenario
    ask()
    ask("two")

    async def run():
        await asyncio.gather(server.inbox_delivery.check(), server.inbox_delivery.check())
        supervisor._last_input = 0
        ask("arrived-after-wake")
        await server.inbox_delivery.check()

    asyncio.run(run())
    assert sent == [b"\x1b[200~" + REMINDER.encode() + b"\x1b[201~\r"]
    assert b"Peer-controlled" not in sent[0]
    assert history.session_api.pending_counts() == {"recipient": 3}


@pytest.mark.parametrize(
    "reason",
    [
        "busy",
        "waiting",
        "approval",
        "attached",
        "input",
        "output",
        "stopped",
        "no_prompt",
        "no_terminal",
        "queued_input",
    ],
)
def test_unsafe_session_is_never_typed_into(scenario, monkeypatch, reason):
    history, server, supervisor, sent, _, ask = scenario
    ask()
    if reason in {"busy", "waiting"}:
        history.set_state("recipient", reason)
    elif reason == "approval":
        server.approvals.register("recipient", "Bash", {}, 1, blocking=True)
    elif reason == "attached":
        supervisor._byte_subs.add(object())
    elif reason == "input":
        supervisor._last_input = time.monotonic()
    elif reason == "output":
        supervisor._last_output = time.monotonic()
    elif reason == "stopped":
        supervisor.running = False
    elif reason == "no_terminal":
        supervisor._tmux_target = None
    elif reason == "queued_input":
        supervisor._input_queue = asyncio.Queue()
        supervisor._input_queue.put_nowait(b"draft")
    else:
        monkeypatch.setattr("duckterm.core.inbox_delivery.inbox_prompt.probe", lambda *_: None)
    asyncio.run(server.inbox_delivery.check())
    assert sent == []
    assert history.session_api.pending_counts() == {"recipient": 1}


def test_prompt_change_or_cancel_during_probe_cannot_send(scenario, monkeypatch):
    _, server, _, sent, call, ask = scenario
    question = ask()
    count = 0

    def probe(*_):
        nonlocal count
        count += 1
        if count == 2:
            call("sender", "POST", f"/questions/{question['id']}/cancel")
        return Prompt("codex", 2, 0, "stable")

    # DB is deliberately only touched on the loop thread, never probe's worker.
    async def in_loop(fn, *args):
        return fn(*args)

    monkeypatch.setattr("duckterm.core.inbox_delivery.asyncio.to_thread", in_loop)
    monkeypatch.setattr("duckterm.core.inbox_delivery.inbox_prompt.probe", probe)
    asyncio.run(server.inbox_delivery.check())
    assert sent == []


def test_retry_budget_survives_restart_and_owner_read_is_not_ack(scenario, tmp_path):
    history, server, _, sent, call, ask = scenario
    question = ask()
    for _ in range(3):
        history.session_api.mark_delivery([question["id"]], 1, "wake_requested", attempted=True)
    reopened = HistoryStore(tmp_path / "db.sqlite")
    assert reopened.session_api.delivery_candidates(int(time.time() * 1000)) == {}
    message = reopened.session_api.inbox("recipient", owner=True)["messages"][0]
    assert message["delivery"]["outcome"] == "needs_attention"
    assert message["delivery"]["last_read_at"] == 0
    reopened.close()
    call("recipient", "GET", "/inbox")
    assert (
        history.session_api.inbox("recipient", owner=True)["messages"][0]["delivery"][
            "last_read_at"
        ]
        > 0
    )
    asyncio.run(server.inbox_delivery.check())
    assert sent == []


@pytest.mark.parametrize(
    "row,tail,expected",
    [
        ("›", "gpt-6-astra · project", True),
        ("\x1b[1m›\x1b[0m \x1b[2mAsk Codex to do anything\x1b[0m", "gpt-6-astra · project", True),
        ("› Ask Codex to do anything", "gpt-6-astra · project", False),
        ("› unfinished draft", "gpt-6-astra · project", False),
        ("›", "  continuation of draft\ngpt-6-astra · project", False),
        ("›", "Press enter to confirm", False),
        ("›", "esc to interrupt", False),
        ("$", "gpt-6-astra · project", False),
    ],
)
def test_codex_prompt_requires_empty_editor(row, tail, expected):
    assert empty_prompt("codex", "codex", 2, 0, row + "\n" + tail) is expected
    assert not empty_prompt("codex", "zsh", 2, 0, row + "\n" + tail)
    assert not empty_prompt("codex", "codex", 5, 0, row + "\n" + tail)
