"""Expensive mail retirement runs on a schedule; deadline expiry stays immediate."""

import contextlib
import sqlite3
import time
from pathlib import Path

import pytest

from duckterm.core import session_api
from duckterm.persistence import mail_analytics
from duckterm.persistence.history import HistoryStore

DAY_MS = 86_400_000


class Clock:
    """Stands in for session_api's time module so only monotonic is controlled."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name: str):
        return getattr(time, name)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(session_api, "time", fake)
    return fake


@pytest.fixture
def store(tmp_path: Path, clock: Clock):
    history = HistoryStore(tmp_path / "db.sqlite")
    for key in ("sender", "recipient"):
        history.record(
            {"event_type": "SessionStart", "session_key": key, "test": True, "_ts": 1, "_id": key}
        )
    try:
        yield history
    finally:
        history.purge_test_sessions()
        history.close()


def insert(conn: sqlite3.Connection, qid: str, **fields) -> None:
    now = int(time.time() * 1000)
    row = {
        "id": qid,
        "sender": "sender",
        "recipient": "recipient",
        "sender_name": "sender",
        "root": "/tmp",
        "question": "q",
        "status": "answered",
        "created_at": now - 9 * DAY_MS,
        "expires_at": 0,
        "answered_at": now - 8 * DAY_MS,
        "idempotency_key": qid,
        "content_hash": qid,
        **fields,
    }
    conn.execute(
        f"INSERT INTO session_questions ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
        tuple(row.values()),
    )
    conn.commit()


def exists(store: HistoryStore, qid: str) -> bool:
    return bool(
        store._conn.execute("SELECT 1 FROM session_questions WHERE id=?", (qid,)).fetchone()
    )


def status(store: HistoryStore, qid: str) -> str:
    return store._conn.execute(
        "SELECT status FROM session_questions WHERE id=?", (qid,)
    ).fetchone()[0]


@pytest.fixture
def spies(store: HistoryStore, monkeypatch):
    calls = {"before_retire": 0, "retire_mail": 0}
    real_before = store.session_api.before_retire
    real_retire = mail_analytics.retire_mail

    def before_retire() -> None:
        calls["before_retire"] += 1
        real_before()

    def retire_mail(*args, **kwargs) -> None:
        calls["retire_mail"] += 1
        real_retire(*args, **kwargs)

    store.session_api.before_retire = before_retire
    monkeypatch.setattr(mail_analytics, "retire_mail", retire_mail)
    return calls


def test_frequent_sweeps_skip_maintenance_until_the_interval_boundary(store, clock, spies):
    insert(store._conn, "old")
    start = clock.now
    for offset in (0, 1, 300, session_api.RETIRE_INTERVAL_SECONDS - 1):
        clock.now = start + offset
        store.session_api._sweep()
        store.session_api.pending_counts()
    assert spies == {"before_retire": 0, "retire_mail": 0}
    assert exists(store, "old")

    clock.now = start + session_api.RETIRE_INTERVAL_SECONDS
    store.session_api._sweep()
    assert spies["before_retire"] == 1 and spies["retire_mail"] >= 1
    assert not exists(store, "old")

    insert(store._conn, "older")
    clock.now += 1
    store.session_api._sweep()
    assert spies["before_retire"] == 1
    assert exists(store, "older")


def test_deadline_expiry_is_immediate_while_retirement_is_not_due(store, clock, spies):
    now = int(time.time() * 1000)
    insert(
        store._conn,
        "late",
        status="queued",
        answered_at=None,
        created_at=now - 1000,
        expires_at=now - 1,
    )
    insert(
        store._conn,
        "open",
        status="queued",
        answered_at=None,
        created_at=now - 1000,
        expires_at=now + DAY_MS,
    )
    clock.now += 1
    counts = store.session_api.pending_counts()
    assert status(store, "late") == "expired"
    assert status(store, "open") == "queued"
    assert counts == {"recipient": 1}
    assert spies["before_retire"] == 0


def test_force_bypasses_the_interval_and_restarts_it(store, clock, spies):
    insert(store._conn, "old")
    clock.now += 1
    store.session_api._sweep(force_retire=True)
    assert spies["before_retire"] == 1
    assert not exists(store, "old")
    insert(store._conn, "older")
    clock.now += session_api.RETIRE_INTERVAL_SECONDS - 1
    store.session_api._sweep()
    assert exists(store, "older")
    clock.now += 1
    store.session_api._sweep()
    assert not exists(store, "older")


@pytest.mark.parametrize("failure", ["before_retire", "retire_sql"])
def test_failed_retirement_is_retried_on_the_next_sweep(store, clock, spies, monkeypatch, failure):
    insert(store._conn, "old")
    clock.now += session_api.RETIRE_INTERVAL_SECONDS
    working_before = store.session_api.before_retire
    working_retire = mail_analytics.retire_mail

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    if failure == "before_retire":
        store.session_api.before_retire = broken
    else:
        monkeypatch.setattr(mail_analytics, "retire_mail", broken)
    # Either propagating or logging the failure is acceptable; skipping the retry is not.
    with contextlib.suppress(sqlite3.OperationalError):
        store.session_api._sweep()
    assert exists(store, "old")

    store.session_api.before_retire = working_before
    monkeypatch.setattr(mail_analytics, "retire_mail", working_retire)
    clock.now += 1  # well inside the interval a success would have started
    store.session_api._sweep()
    assert not exists(store, "old")


def test_reopening_the_store_retires_eligible_mail_at_startup(tmp_path, clock):
    path = tmp_path / "db.sqlite"
    first = HistoryStore(path)
    for key in ("sender", "recipient"):
        first.record(
            {"event_type": "SessionStart", "session_key": key, "test": True, "_ts": 1, "_id": key}
        )
    insert(first._conn, "old")
    insert(first._conn, "recent", answered_at=int(time.time() * 1000) - DAY_MS)
    first.close()

    reopened = HistoryStore(path)  # same monotonic instant: only the startup force can run it
    try:
        assert not exists(reopened, "old")
        assert exists(reopened, "recent")
    finally:
        reopened.purge_test_sessions()
        reopened.close()
