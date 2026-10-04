"""Retention, failure atomicity, UTC attribution and content-free analytics."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from duckterm.core.session_api import NO_DEADLINE
from duckterm.persistence import mail_analytics as mail
from duckterm.persistence.history import HistoryStore

NOW = int(datetime(2026, 9, 28, 12, tzinfo=UTC).timestamp() * 1000)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: NOW / 1000)
    result = HistoryStore(tmp_path / "db.sqlite")
    yield result
    result.close()


def seed(
    store,
    key="q",
    *,
    status="answered",
    created=None,
    ended=None,
    kind="question",
    expires=NO_DEADLINE,
    sender="a",
):
    created = NOW - 9 * mail.DAY if created is None else created
    ended = created + 120_000 if ended is None and status == "answered" else ended
    store._conn.execute(
        """INSERT INTO session_questions
        (id,sender,recipient,sender_name,root,question,status,answer,created_at,expires_at,
         answered_at,idempotency_key,content_hash,kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            key,
            sender,
            "b",
            "PRIVATE NAME",
            "root",
            "PRIVATE QUESTION",
            status,
            "PRIVATE ANSWER",
            created,
            expires,
            ended,
            key,
            "hash",
            kind,
        ),
    )
    store._conn.commit()


def snapshot(store, days=None):
    return mail.snapshot(store._conn, days, now=NOW)


def test_exactly_once_before_after_sweep_and_reopen(store, tmp_path):
    seed(store)
    before = snapshot(store)
    assert sum(d["sent"] for d in before["daily"]) == 1
    assert sum(d["answered"] for d in before["daily"]) == 1
    store.session_api._sweep()
    assert store._conn.execute("SELECT COUNT(*) FROM session_questions").fetchone()[0] == 0
    assert snapshot(store) == before
    store.session_api._sweep()
    assert snapshot(store) == before
    reopened = HistoryStore(tmp_path / "db.sqlite")
    assert snapshot(reopened) == before
    reopened.close()
    rows = list(store._conn.execute("SELECT * FROM mail_rollup_v1"))
    assert "PRIVATE" not in json.dumps([list(row) for row in rows])
    assert not {"question", "answer", "id", "sender_name"} & {
        r["name"] for r in store._conn.execute("PRAGMA table_info(mail_rollup_v1)")
    }


def test_failed_delete_rolls_back_counts(store):
    seed(store)
    store._conn.execute(
        "CREATE TRIGGER fail_delete BEFORE DELETE ON session_questions "
        "BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.session_api._sweep()
    assert store._conn.execute("SELECT COUNT(*) FROM mail_rollup_v1").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM session_questions").fetchone()[0] == 1
    store._conn.execute("DROP TRIGGER fail_delete")
    store.session_api._sweep()
    assert sum(d["sent"] for d in snapshot(store)["daily"]) == 1


def test_utc_completion_and_sent_cohort_are_separate(store):
    sent = NOW - 3 * mail.DAY
    ended = NOW - mail.DAY
    seed(store, created=sent, ended=ended)
    data = snapshot(store)
    daily = {d["day"]: d for d in data["daily"]}
    assert daily[mail.day(sent)]["sent"] == 1
    assert daily[mail.day(sent)]["answered"] == 0
    assert daily[mail.day(ended)]["answered"] == 1
    assert daily[mail.day(ended)]["duration_sum_ms"] == ended - sent
    assert daily[mail.day(ended)]["histogram"][-1] == 1
    assert data["cohorts"] == [
        dict(day=mail.day(sent), sent=1, answered=1, declined=0, expired=0, cancelled=0)
    ]
    recent = snapshot(store, 2)
    assert sum(d["sent"] for d in recent["daily"]) == 0
    assert sum(d["answered"] for d in recent["daily"]) == 1


def test_expiry_and_open_now(store):
    seed(store, "expired", status="queued", expires=NOW - 8 * mail.DAY)
    seed(store, "queued", status="queued", created=NOW - 1000)
    seed(store, "accepted", status="accepted", created=NOW - 2000)
    store.session_api._sweep()
    data = snapshot(store)
    assert sum(d["expired"] for d in data["daily"]) == 1
    assert data["open_now"]["queued"] == data["open_now"]["accepted"] == 1
    assert [r["id"] for r in data["open_now"]["oldest"]] == ["accepted", "queued"]
    assert "PRIVATE" not in json.dumps(data)


def test_session_removal_preserves_counts(store):
    seed(store, created=NOW - 1000)
    before = snapshot(store)
    store.delete_session("a")
    assert snapshot(store) == before


def test_test_sessions_are_never_counted(store):
    store.record(
        {"_id": "test", "_ts": NOW, "event_type": "SessionStart", "session_key": "a", "test": True}
    )
    seed(store)
    store.record(
        {
            "_id": "nudge",
            "_ts": NOW - 40 * mail.DAY,
            "event_type": "OracleNudge",
            "session_key": "a",
            "test": True,
        }
    )
    assert snapshot(store)["daily"] == []
    store.session_api._sweep()
    store.delete_session("a")
    assert snapshot(store)["daily"] == []


def test_broadcast_and_nudge_survive_retirement(store):
    seed(store, kind="broadcast", status="queued")
    store._conn.execute(
        "INSERT INTO events (id,session_key,event_type,ts,payload_json) VALUES (?,?,?,?,?)",
        ("nudge", "b", "OracleNudge", NOW - 40 * mail.DAY, "{}"),
    )
    store._conn.commit()
    before = snapshot(store)
    store.session_api._sweep()
    mail.retire_events(store._conn, "ts < ?", (NOW - 30 * mail.DAY,))
    assert snapshot(store) == before
    assert sum(d["broadcasts"] for d in before["daily"]) == 1
    assert sum(d["nudges"] for d in before["daily"]) == 1
    assert sum(d["sent"] for d in before["daily"]) == 0


def test_v5_migration_counts_retained_rows_only(store, tmp_path):
    seed(store)
    store._conn.execute("DROP TABLE mail_rollup_v1")
    store._conn.execute("DROP TABLE mail_analytics_meta")
    store._conn.execute("PRAGMA user_version=5")
    store._conn.commit()
    reopened = HistoryStore(tmp_path / "db.sqlite")
    assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 11
    assert sum(d["sent"] for d in snapshot(reopened)["daily"]) == 1
    assert reopened._conn.execute("SELECT COUNT(*) FROM session_questions").fetchone()[0] == 0
    reopened.close()


def test_v6_restart_migration_preserves_retired_mail_counts(store, tmp_path):
    seed(store)
    store.session_api._sweep()
    before = snapshot(store)
    store._conn.execute("ALTER TABLE sessions DROP COLUMN restart_json")
    store._conn.execute("PRAGMA user_version=6")
    store._conn.commit()
    reopened = HistoryStore(tmp_path / "db.sqlite")
    try:
        assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 11
        assert snapshot(reopened) == before
        assert "restart_json" in {
            row["name"] for row in reopened._conn.execute("PRAGMA table_info(sessions)")
        }
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "duration,bucket",
    [
        (0, 0),
        (59999, 0),
        (60000, 1),
        (300000, 2),
        (900000, 3),
        (3600000, 4),
        (14400000, 5),
        (86400000, 6),
    ],
)
def test_histogram_boundaries_and_exact_mean(store, duration, bucket):
    seed(store, created=NOW - duration - 1, ended=NOW - 1)
    entry = next(d for d in snapshot(store)["daily"] if d["duration_count"])
    assert entry["histogram"][bucket] == 1
    assert entry["mean_ms"] == entry["slowest_ms"] == duration
    assert entry["median_approx_ms"] is not None


def test_mixed_live_and_retired_rows_merge_counts_and_durations(store):
    # Same day/pair/bucket must combine, rather than overwrite or double-count.
    sent = NOW - 9 * mail.DAY
    seed(store, "one", created=sent, ended=sent + 120_000)
    seed(store, "two", created=sent, ended=sent + 180_000)
    before = snapshot(store)
    mail.retire_mail(store._conn, "id=?", ("one",))
    assert snapshot(store) == before
    entry = before["daily"][0]
    assert entry["sent"] == entry["answered"] == 2
    assert entry["histogram"][1] == 2
    assert entry["mean_ms"] == 150_000 and entry["slowest_ms"] == 180_000
    assert before["top_pairs"] == [dict(sender="a", recipient="b", sent=2)]
    mail.retire_mail(store._conn, "id=?", ("two",))
    assert snapshot(store) == before


def test_folder_scope_includes_either_endpoint_once_before_and_after_retirement(store):
    seed(store, "inside", sender="inside")
    seed(store, "outside", sender="outside")
    seed(store, "open", sender="inside", status="queued", created=NOW - 1000)
    selected = mail.snapshot(store._conn, None, now=NOW, sessions={"inside"})
    assert sum(row["sent"] for row in selected["daily"]) == 2
    assert sum(row["answered"] for row in selected["daily"]) == 1
    assert selected["open_now"]["queued"] == 1
    both = mail.snapshot(store._conn, None, now=NOW, sessions={"inside", "b"})
    assert sum(row["sent"] for row in both["daily"]) == 3
    store.session_api._sweep()
    assert mail.snapshot(store._conn, None, now=NOW, sessions={"inside"}) == selected
    assert mail.snapshot(store._conn, None, now=NOW, sessions=set())["daily"] == []
