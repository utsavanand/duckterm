"""Event expiry preserves checkpoint policy without parsing evidence per event."""

import itertools
import json
import random
import sqlite3

import pytest

from duckterm.persistence import mail_analytics
from duckterm.persistence.history import HistoryStore
from duckterm.persistence.saved_state import MARKER_FORMAT, retention_predicate

# Frozen pre-optimization predicate: independent oracle for compatibility,
# including SQL NULL semantics and conservative handling of damaged markers.
OLD = f"""NOT EXISTS (SELECT 1 FROM checkpoints c WHERE c.session_key=e.session_key AND
 CASE WHEN NOT json_valid(c.record_json) THEN 1
 WHEN json_type(c.record_json) IS NOT 'object' THEN 1
 WHEN json_type(c.record_json,'$.format') IS NULL THEN 0
 WHEN json_extract(c.record_json,'$.format') = 'checkpoint_fork_merge_v1' THEN 0
 WHEN json_extract(c.record_json,'$.format') != '{MARKER_FORMAT}' THEN 1
 WHEN json_type(c.record_json,'$.events.first') IS NOT 'integer'
   OR json_type(c.record_json,'$.events.last') IS NOT 'integer'
   OR json_type(c.record_json,'$.mail_ids') IS NOT 'array' THEN 1
 ELSE (e.rowid BETWEEN json_extract(c.record_json,'$.events.first')
   AND json_extract(c.record_json,'$.events.last')) END)"""


def marker(first=2, last=4, **fields):
    return json.dumps(
        {
            "format": MARKER_FORMAT,
            "events": {"first": first, "last": last},
            "mail_ids": [],
            **fields,
        }
    )


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE events(session_key TEXT);"
        "CREATE TABLE checkpoints(session_key TEXT,record_json TEXT);"
    )
    db.executemany("INSERT INTO events VALUES(?)", [("a",)] * 8 + [("b",)] * 8)
    yield db
    db.close()


def selected(conn, predicate):
    return {r[0] for r in conn.execute("SELECT rowid FROM events e WHERE " + predicate)}


def test_exact_candidates_match_original_for_damaged_and_mixed_markers(conn):
    missing = object()
    values = [missing, None, -1, 0, 1, 4, 8, 10, 1.0, "1", True, {}, []]
    mail = [missing, None, [], ["one"], {}, 0, "text"]
    records = [
        None,
        "{",
        "null",
        "[]",
        "true",
        "0",
        '"text"',
        "{}",
        marker(format=None),
        marker(format="unknown"),
        marker(format="checkpoint_fork_merge_v1"),
    ]
    for first, last, ids in itertools.product(values, values, mail):
        obj = {"format": MARKER_FORMAT, "events": {}}
        if first is not missing:
            obj["events"]["first"] = first
        if last is not missing:
            obj["events"]["last"] = last
        if ids is not missing:
            obj["mail_ids"] = ids
        records.append(json.dumps(obj))
    for raw in records:
        conn.execute("DELETE FROM checkpoints")
        conn.execute("INSERT INTO checkpoints VALUES('a',?)", (raw,))
        assert selected(conn, retention_predicate("events")) == selected(conn, OLD), raw
    rng = random.Random(193)
    for _ in range(200):
        conn.execute("DELETE FROM checkpoints")
        conn.executemany(
            "INSERT INTO checkpoints VALUES(?,?)",
            [
                (rng.choice(["a", "b", "other"]), rng.choice(records))
                for _ in range(rng.randrange(30))
            ],
        )
        assert selected(conn, retention_predicate("events")) == selected(conn, OLD)


def test_inclusive_bounds_scope_and_overlapping_checkpoints(conn):
    conn.executemany(
        "INSERT INTO checkpoints VALUES(?,?)",
        [("a", marker(2, 4)), ("a", marker(4, 6)), ("other", "{")],
    )
    assert selected(conn, retention_predicate("events")) == set(range(1, 17)) - set(range(2, 7))


def test_json_work_does_not_grow_with_candidate_events(conn):
    conn.execute(
        "INSERT INTO checkpoints VALUES('a',?)", (marker(1, 10000, evidence="x" * 100000),)
    )
    calls = 0
    with sqlite3.connect(":memory:") as json_db:

        def counted_valid(value):
            nonlocal calls
            calls += 1
            return json_db.execute("SELECT json_valid(?)", (value,)).fetchone()[0]

        conn.create_function("json_valid", 1, counted_valid)
        selected(conn, retention_predicate("events"))
        small = calls
        calls = 0
        conn.executemany("INSERT INTO events VALUES('a')", [()] * 2000)
        selected(conn, retention_predicate("events"))
        assert 0 < calls == small <= 3


def test_startup_expiry_and_analytics_are_exactly_once(tmp_path):
    path = tmp_path / "db.sqlite"
    store = HistoryStore(path)
    # Disposable DB only. Orphan event rows exercise nudge aggregation without
    # enrolling sessions or issuing credentials.
    store._conn.executemany(
        "INSERT INTO events(id,session_key,event_type,ts,payload_json) "
        "VALUES(?,'a','OracleNudge',1,'{}')",
        [("one",), ("two",), ("three",)],
    )
    store._conn.execute(
        "INSERT INTO checkpoints(id,session_key,label,created_at,record_json) "
        "VALUES('pin','a','test',1,?)",
        (marker(2, 2),),
    )
    store._conn.commit()
    store.close()
    for _ in range(2):
        store = HistoryStore(path)
        try:
            assert [r[0] for r in store._conn.execute("SELECT id FROM events")] == ["two"]
            assert store._conn.execute("SELECT SUM(count) FROM mail_rollup_v1").fetchone()[0] == 2
        finally:
            store.close()


def test_failed_expiry_rolls_back_analytics(tmp_path):
    store = HistoryStore(tmp_path / "db.sqlite")
    try:
        store._conn.execute(
            "INSERT INTO events(id,session_key,event_type,ts,payload_json) "
            "VALUES('one','a','OracleNudge',1,'{}')"
        )
        store._conn.execute(
            "CREATE TRIGGER refuse BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'refuse'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="refuse"):
            mail_analytics.retire_events(
                store._conn, "ts < ? AND " + retention_predicate("events"), (2,)
            )
        assert store._conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert store._conn.execute("SELECT count(*) FROM mail_rollup_v1").fetchone()[0] == 0
    finally:
        store.close()
