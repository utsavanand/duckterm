"""Independent saved-state boundaries; fake providers, real SQLite, controlled races."""

import asyncio
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from duckterm.core.saved_progress import ProgressCoordinator
from duckterm.persistence.digests import DigestStore
from duckterm.persistence.history import HistoryStore
from duckterm.persistence.saved_state import MARKER_FORMAT, event_source


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    history = HistoryStore(tmp_path / "state.sqlite")
    history.record(
        {
            "_id": "start",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "a",
            "runtime": "codex",
            "test": True,
        }
    )
    digests = DigestStore(conn=history._conn)
    transcript = [{"role": "user", "text": "Preserve my current work"}]
    server = SimpleNamespace(
        history=history,
        digests=digests,
        _progress_transcript=lambda *args: list(transcript),
        _message_source=lambda key: None,
        orchestrator=SimpleNamespace(get=lambda key: None),
    )
    coordinator = ProgressCoordinator(server)
    yield history, digests, coordinator, transcript
    history.purge_test_sessions()
    digests.close()
    history.close()


def reply(summary="Useful state", *, validation=True):
    return SimpleNamespace(
        text=json.dumps(
            {
                "accept": [{"bucket": "deliverables", "text": "Saved work"}],
                "done_next_action_ids": [],
                "summary_validation": {"ready": True, "reason_codes": []},
            }
            if validation
            else {
                "summary": summary,
                "deliverables": ["Saved work"],
                "learnings": [],
                "user_learnings": [],
                "next_actions": [],
            }
        )
    )


def provider(monkeypatch, replies):
    calls = []
    values = iter(replies)

    def summarize(prompt):
        calls.append(prompt)
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("duckterm.server.summarize", summarize)
    return calls


def test_refresh_rollback_has_no_partial_items_revision_or_cache(rig, monkeypatch, tmp_path):
    history, digests, coordinator, _ = rig
    provider(monkeypatch, [reply(validation=False), reply()])
    history._conn.execute(
        "CREATE TRIGGER fail_cache BEFORE UPDATE OF progress ON sessions "
        "BEGIN SELECT RAISE(ABORT,'cache write failed'); END"
    )
    history._conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="cache write failed"):
        asyncio.run(coordinator.refresh("a"))
    assert not digests.items("a")
    assert history.session("a")["progress"] is None
    with sqlite3.connect(tmp_path / "state.sqlite") as reopened:
        assert reopened.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 0
        assert (
            reopened.execute("SELECT progress FROM sessions WHERE session_key='a'").fetchone()[0]
            is None
        )


def test_coalesced_refresh_survives_one_waiter_cancel_and_reuses_without_calls(rig, monkeypatch):
    _, digests, coordinator, _ = rig
    started, release = threading.Event(), threading.Event()
    calls = []

    def summarize(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            started.set()
            assert release.wait(5), "generation not released"
            return reply(validation=False)
        return reply()

    monkeypatch.setattr("duckterm.server.summarize", summarize)

    async def run():
        captured = await coordinator.capture("a")
        first = asyncio.create_task(coordinator.refresh("a", captured))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            second = asyncio.create_task(coordinator.refresh("a", captured))
            await asyncio.sleep(0)  # schedule waiter, not a wall-clock race
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            revision = await asyncio.wait_for(second, 5)
            assert revision["summary_validation"]["ready"] is True
            assert (await coordinator.refresh("a"))["id"] == revision["id"]
            assert len(calls) == 2
            assert all(row["bucket"] != "summary_revision_v1" for row in digests.items("a"))
        finally:
            release.set()

    asyncio.run(run())


def test_older_same_generation_completion_cannot_replace_newer_cache(rig, monkeypatch):
    history, _, coordinator, transcript = rig
    started, release = threading.Event(), threading.Event()
    calls = []
    lock = threading.Lock()

    def summarize(prompt):
        with lock:
            calls.append(prompt)
            number = len(calls)
        if number == 1:
            started.set()
            assert release.wait(5)
            return reply("Old summary", validation=False)
        if number == 2:
            return reply("New summary", validation=False)
        return reply()

    monkeypatch.setattr("duckterm.server.summarize", summarize)

    async def run():
        old = asyncio.create_task(coordinator.refresh("a"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            transcript.append({"role": "user", "text": "New owner constraint"})
            new = await coordinator.refresh("a")
            release.set()
            await asyncio.wait_for(old, 5)
            current = json.loads(history.session("a")["progress"])
            assert current["revision_id"] == new["id"]
            assert current["summary"] == "New summary"
        finally:
            release.set()

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["bad_generation", "bad_validation", "missing_paragraph_verdict"]
)
def test_failure_does_not_replace_last_ready_revision(rig, monkeypatch, failure):
    history, _, coordinator, transcript = rig
    broken = SimpleNamespace(text="not valid JSON")
    next_replies = (
        [broken]
        if failure == "bad_generation"
        else [
            reply("Unverified replacement", validation=False),
            (
                broken
                if failure == "bad_validation"
                else SimpleNamespace(text=json.dumps({"accept": [], "done_next_action_ids": []}))
            ),
        ]
    )
    provider(monkeypatch, [reply(validation=False), reply(), *next_replies])

    async def run():
        initial = await coordinator.refresh("a")
        transcript.append({"role": "user", "text": "More work"})
        result = await coordinator.refresh("a")
        assert result is None or not result["summary_validation"]["ready"]
        assert json.loads(history.session("a")["progress"])["revision_id"] == initial["id"]

    asyncio.run(run())


def test_checkpoint_pins_old_events_but_unreferenced_rows_expire_on_reopen(rig, tmp_path):
    history, _, _, _ = rig
    for key in ("a", "b"):
        history.record(
            {
                "_id": f"old-{key}",
                "_ts": 2,
                "event_type": "UserPromptSubmit",
                "session_key": key,
                "prompt": "Old work",
                "test": True,
            }
        )
    captured = event_source(history._conn, "a")
    history.add_checkpoint(
        checkpoint_id="marker",
        session_key="a",
        label="Keep",
        summary="",
        record={"format": MARKER_FORMAT, "events": captured, "mail_ids": []},
        markdown_path=None,
        created_at=3,
    )
    reopened = HistoryStore(tmp_path / "state.sqlite")
    try:
        assert event_source(reopened._conn, "a") == captured
        assert event_source(reopened._conn, "b")["count"] == 0
    finally:
        reopened.close()


def test_explicit_refresh_can_recover_unverified_summary_without_new_source(rig, monkeypatch):
    _, _, coordinator, _ = rig
    calls = provider(
        monkeypatch,
        [
            reply(validation=False),
            SimpleNamespace(text="invalid verdict"),
            reply(validation=False),
            reply(),
        ],
    )

    async def run():
        first = await coordinator.refresh("a")
        assert first["summary_validation"]["ready"] is False
        recovered = await coordinator.refresh("a")
        assert recovered["summary_validation"]["ready"] is True
        assert len(calls) == 4

    asyncio.run(run())


def test_policy_changed_during_generation_cannot_promote_old_policy_as_ready(rig, monkeypatch):
    history, _, coordinator, _ = rig
    started, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "synthetic-policy-A")

    def summarize(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
            return reply(validation=False)
        return reply()

    monkeypatch.setattr("duckterm.server.summarize", summarize)

    async def run():
        task = asyncio.create_task(coordinator.refresh("a"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "synthetic-policy-B")
            release.set()
            revision = await asyncio.wait_for(task, 5)
            current = json.loads(history.session("a")["progress"] or "{}")
            assert (
                revision is None
                or current.get("revision_id") != revision["id"]
                or not revision["summary_validation"]["ready"]
            )
        finally:
            release.set()

    asyncio.run(run())
