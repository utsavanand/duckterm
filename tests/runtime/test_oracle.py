"""Oracle pastes a fixed inbox reminder into idle agents, and only when no
draft can be sitting in their input box."""

import asyncio
import time

import pytest
from tests.runtime.test_session_api import ask, enroll

from duckterm.core import oracle
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.runtimes.codex import CodexRuntime
from duckterm.server import Server

HOUR = 3_600_000
CLAUDE_EMPTY = "\x1b[38;5;244m────\n\x1b[39m❯\xa0\n\x1b[38;5;244m────\n  status line"
CLAUDE_DRAFT = "\x1b[38;5;244m────\n\x1b[39m❯\xa0fix the flaky test\n────"
# Captured from a live idle session: Claude's suggested next prompt, dimmed.
CLAUDE_SUGGESTION = "────\n\x1b[39m❯\xa0\x1b[2mcheck inbox\x1b[0m\n────"
CODEX_EMPTY = "\x1b[1m›\x1b[0m \x1b[2mAsk Codex to do anything\x1b[0m\n  gpt model · ~/repo"
CODEX_DRAFT = "\x1b[1m›\x1b[0m ship the release\n  gpt model · ~/repo"


@pytest.mark.parametrize(
    ("runtime", "screen", "empty"),
    [
        (ClaudeCodeRuntime(), CLAUDE_EMPTY, True),
        (ClaudeCodeRuntime(), CLAUDE_DRAFT, False),
        (ClaudeCodeRuntime(), CLAUDE_SUGGESTION, True),
        (ClaudeCodeRuntime(), "no prompt visible", False),
        (CodexRuntime(), CODEX_EMPTY, True),
        (CodexRuntime(), CODEX_DRAFT, False),
    ],
)
def test_prompt_is_empty_ignores_placeholders_but_not_drafts(runtime, screen, empty) -> None:
    assert runtime.prompt_is_empty(screen) is empty


NOW = 100 * HOUR
OLD_PEER = {"id": "q1", "kind": "question", "status": "queued", "created_at": NOW - 47 * HOUR}
GATES = dict(
    state="idle",
    turn_ended_ms=NOW - HOUR,
    observed_since_ms=NOW - 2 * HOUR,
    last_owner_input_ms=NOW - 3 * HOUR,
    prompt_empty=True,
    mail=[OLD_PEER],
    previous=None,
    now_ms=NOW,
)


@pytest.mark.parametrize(
    ("change", "nudges"),
    [
        ({}, True),
        ({"state": "waiting"}, False),
        ({"prompt_empty": False}, False),
        ({"turn_ended_ms": NOW - 60_000}, False),  # not settled
        ({"last_owner_input_ms": NOW - 30 * 60_000}, False),  # typed after the turn ended
        # Turn ended before this server watched: keystroke memory is blank, screen decides.
        ({"observed_since_ms": NOW - 30 * 60_000, "last_owner_input_ms": 0}, True),
        ({"mail": [{**OLD_PEER, "created_at": NOW - 60_000}]}, False),  # fresh peer mail
        ({"mail": [{**OLD_PEER, "last_read_at": NOW - HOUR}]}, False),  # read, left queued
        ({"mail": [{**OLD_PEER, "status": "accepted", "last_read_at": NOW - HOUR}]}, True),
        ({"mail": [{**OLD_PEER, "kind": "broadcast", "created_at": NOW - 60_000}]}, True),
        ({"previous": oracle.Nudge(frozenset({"q1"}), NOW - 5 * HOUR)}, False),  # same mail
        ({"previous": oracle.Nudge(frozenset({"q0"}), NOW - 10 * 60_000)}, False),  # rate limit
        ({"previous": oracle.Nudge(frozenset({"q0"}), NOW - 2 * HOUR)}, True),  # new mail
    ],
)
def test_should_nudge_gates(change, nudges) -> None:
    assert bool(oracle.should_nudge(**{**GATES, **change})) is nudges


class FakeSupervisor:
    def __init__(self, screen: str) -> None:
        self.running = True
        self.runtime = CodexRuntime()
        self.screen = screen
        self.observed_since_ms = 0
        self.last_owner_input_ms = 0
        self.pasted: list[bytes] = []

    def visible_screen(self) -> str:
        return self.screen

    def write_bytes(self, data: bytes) -> bool:
        self.pasted.append(data)
        return True


@pytest.fixture
def idle_recipient(tmp_path, monkeypatch):
    history = HistoryStore(tmp_path / "db.sqlite")
    long_ago = int(time.time() * 1000) - 2 * HOUR
    for key in ("a", "b"):
        history.record(
            {
                "_id": key,
                "_ts": long_ago,
                "event_type": "SessionStart",
                "session_key": key,
                "test": True,
                "runtime": "codex",
            }
        )
        history.set_meta(key, name=key, group="work")
    ask(history, enroll(history, "a"))
    enroll(history, "b")
    history.record({"_id": "stop", "_ts": long_ago, "event_type": "Stop", "session_key": "b"})
    history._conn.execute("UPDATE session_questions SET created_at = ?", (long_ago,))
    history._conn.commit()
    server = Server(history=history)
    sup = FakeSupervisor(CODEX_EMPTY)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: sup if key == "b" else None)
    yield server, sup
    history.close()


def test_tick_pastes_fixed_reminder_once_without_peer_text(idle_recipient) -> None:
    server, sup = idle_recipient
    asyncio.run(server._oracle_tick())
    asyncio.run(server._oracle_tick())
    assert len(sup.pasted) == 1
    text = sup.pasted[0].decode()
    assert text.startswith("\x1b[200~Duckterm Oracle: you have 1 inbox item waiting")
    assert text.endswith("\x1b[201~\r")
    assert "What contract should I use?" not in text


def test_tick_skips_mail_the_agent_already_read(idle_recipient) -> None:
    server, sup = idle_recipient
    server.history.session_api.inbox("b")
    asyncio.run(server._oracle_tick())
    assert sup.pasted == []


def test_tick_leaves_a_draft_alone(idle_recipient) -> None:
    server, sup = idle_recipient
    sup.screen = CODEX_DRAFT
    asyncio.run(server._oracle_tick())
    assert sup.pasted == []
