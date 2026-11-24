"""Native task snapshots and owner questions must agree across turns and reloads."""

import asyncio
import json
import time
from contextlib import closing

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.core.eventbus import EventBus
from duckterm.core.relay import permission_question
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

RUNNING = [{"id": "shell-1", "type": "shell", "status": "running"}]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=history)
    server.bus.publish(
        {
            "event_type": "SessionStart",
            "session_key": "test-bg",
            "runtime": "claude-code",
            "test": True,
        }
    )
    yield server, history
    history.close()


def emit(server, event_type, **fields):
    return server.bus.publish({"event_type": event_type, "session_key": "test-bg", **fields})


def test_stop_with_background_shell_stays_busy_until_empty_snapshot(world):
    server, history = world
    emit(server, "Stop", background_tasks=RUNNING)
    assert history.session("test-bg")["state"] == "busy"
    idle = emit(server, "Notification", notification_type="idle_prompt")
    assert idle["background_tasks"] == RUNNING
    stored = json.loads(
        history._conn.execute(
            "SELECT payload_json FROM events WHERE id = ?", (idle["_id"],)
        ).fetchone()[0]
    )
    assert "background_tasks" not in stored
    assert history.session("test-bg")["state"] == "busy"
    emit(server, "Stop", background_tasks=[])
    assert history.session("test-bg")["state"] == "idle"


def test_subagent_snapshot_settles_only_completed_turn(world):
    server, history = world
    emit(server, "Stop", background_tasks=RUNNING)
    emit(server, "SubagentStop", agent_id="child", background_tasks=[])
    assert history.session("test-bg")["state"] == "idle"
    emit(server, "PreToolUse", tool_name="Bash")
    emit(server, "SubagentStop", agent_id="child", background_tasks=[])
    assert history.session("test-bg")["state"] == "busy"


@pytest.mark.parametrize("status", ["completed", "failed", "killed", "cancelled"])
def test_finished_background_tasks_do_not_keep_session_busy(world, status):
    server, history = world
    emit(server, "Stop", background_tasks=[{**RUNNING[0], "status": status}])
    assert history.session("test-bg")["state"] == "idle"


def test_native_snapshot_survives_store_reopen_and_resets_on_new_run(tmp_path):
    path = tmp_path / "history.sqlite"
    with closing(HistoryStore(path)) as history:
        bus = EventBus(sink=history.record)
        bus.publish({"event_type": "SessionStart", "session_key": "bg", "test": True})
        bus.publish({"event_type": "Stop", "session_key": "bg", "background_tasks": RUNNING})
    with closing(HistoryStore(path)) as history:
        bus = EventBus(sink=history.record)
        bus.publish(
            {"event_type": "Notification", "notification_type": "idle_prompt", "session_key": "bg"}
        )
        assert history.session("bg")["state"] == "busy"
        bus.publish({"event_type": "SessionEnd", "session_key": "bg"})
        bus.publish(
            {"event_type": "SubagentStop", "session_key": "bg", "background_tasks": RUNNING}
        )
        assert history.session("bg")["state"] == "terminated"
        bus.publish({"event_type": "SessionStart", "session_key": "bg", "test": True})
        bus.publish({"event_type": "Stop", "session_key": "bg"})
        assert history.session("bg")["state"] == "idle"


def test_explicit_permission_question_wins_over_background_work_until_answered(world):
    server, history = world
    event = emit(
        server,
        "Stop",
        background_tasks=RUNNING,
        last_assistant_message=(
            "Tests passed.\n\nCan I push it and open the PR for release-dev? "
            "It's a server-only change."
        ),
    )
    assert event["owner_waiting"] is True
    assert history.session("test-bg")["state"] == "waiting"
    emit(server, "Notification", notification_type="idle_prompt")
    assert history.session("test-bg")["state"] == "waiting"
    emit(server, "PostToolUse", tool_name="Bash")
    assert history.session("test-bg")["state"] == "waiting"
    rows = dispatch(server, "GET", "/sessions", {"x-duckterm-token": server.token})[1]["sessions"]
    assert rows[0]["state"] == "waiting"
    emit(server, "UserPromptSubmit", prompt="Yes, push it")
    assert history.session("test-bg")["state"] == "busy"
    assert not server._owner_question_pending("test-bg")
    emit(server, "Stop", background_tasks=[])
    assert history.session("test-bg")["state"] == "idle"


def test_late_classifier_cannot_raise_answered_question(world, monkeypatch):
    from duckterm.llm.summarizer import Summary

    server, history = world
    emit(server, "Stop")
    server._RELAY_SETTLE_S = 0
    monkeypatch.setattr(server, "_read_messages", lambda source: [])

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        original = asyncio.to_thread

        async def thread(func, *args, **kwargs):
            if func.__name__ == "summarize":
                started.set()
                await release.wait()
                return Summary(
                    text=json.dumps({"kind": "blocked", "ask": "Approve?", "options": []}),
                    backend="test",
                )
            return await original(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", thread)
        task = asyncio.create_task(
            server._relay_detect_question(
                "test-bg", int(time.time() * 1000), "Do you approve this approach?"
            )
        )
        await asyncio.wait_for(started.wait(), 2)
        emit(server, "UserPromptSubmit", prompt="Yes")
        release.set()
        await task

    asyncio.run(run())
    assert not server._owner_question_pending("test-bg")
    assert history.session("test-bg")["state"] == "busy"


@pytest.mark.parametrize(
    "text",
    [
        "Can I help with anything else?",
        "> Can I push this?",
        "Example: Can I push this?",
        "Pushed it. Everything passed.",
    ],
)
def test_permission_fast_path_excludes_quotes_examples_and_offers(world, text):
    server, history = world
    assert permission_question(text) is None
    emit(server, "Stop", background_tasks=[], last_assistant_message=text)
    assert history.session("test-bg")["state"] == "idle"
    assert not server._owner_question_pending("test-bg")


def test_restart_clears_old_owner_question(world):
    server, history = world
    emit(server, "Stop", last_assistant_message="May I deploy this?")
    assert history.session("test-bg")["state"] == "waiting"
    emit(server, "SessionStart", test=True)
    assert not server._owner_question_pending("test-bg")
    assert history.session("test-bg")["state"] == "busy"


def test_optional_offer_does_not_override_background_work(world, monkeypatch):
    from duckterm.llm.summarizer import Summary

    server, history = world
    emit(server, "Stop", background_tasks=RUNNING)
    server._RELAY_SETTLE_S = 0
    monkeypatch.setattr(server, "_read_messages", lambda source: [])
    monkeypatch.setattr(
        "duckterm.server.summarize",
        lambda *a, **kw: Summary(
            text='{"kind":"offer","ask":"Want more?","options":[]}', backend="test"
        ),
    )
    asyncio.run(server._relay_detect_question("test-bg", int(time.time() * 1000), "Want more?"))
    assert history.session("test-bg")["state"] == "busy"
    assert not server._owner_question_pending("test-bg")


def test_queued_detector_does_not_read_a_newer_turn(world, monkeypatch):
    server, _ = world
    old = emit(server, "Stop")
    emit(server, "UserPromptSubmit", prompt="Already approved")
    server._RELAY_SETTLE_S = 0

    def unexpected(*args):
        pytest.fail("stale detection must not read another turn")

    monkeypatch.setattr(server, "_read_messages", unexpected)
    asyncio.run(server._relay_detect_question("test-bg", old["_ts"], "Approve this?", old["_id"]))
    assert not server._owner_question_pending("test-bg")
