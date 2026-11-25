"""Query optimization preserves exact retained IDs, including corrupt markers."""

import json
import sqlite3

import pytest

from duckterm.persistence.saved_state import MARKER_FORMAT, retention_predicate

IDS = {"keep", "drop", 'quote"\\id', "郵件🦆", "1"}

# Frozen original policy, independent of how the current query is structured.
OLD = f"""NOT EXISTS (SELECT 1 FROM checkpoints c
 WHERE (c.session_key=q.recipient OR c.session_key=q.sender) AND
 CASE WHEN NOT json_valid(c.record_json) THEN 1
 WHEN json_type(c.record_json) IS NOT 'object' THEN 1
 WHEN json_type(c.record_json,'$.format') IS NULL THEN 0
 WHEN json_extract(c.record_json,'$.format') = 'checkpoint_fork_merge_v1' THEN 0
 WHEN json_extract(c.record_json,'$.format') != '{MARKER_FORMAT}' THEN 1
 WHEN json_type(c.record_json,'$.events.first') IS NOT 'integer'
   OR json_type(c.record_json,'$.events.last') IS NOT 'integer'
   OR json_type(c.record_json,'$.mail_ids') IS NOT 'array' THEN 1
 ELSE EXISTS (SELECT 1 FROM json_each(c.record_json,'$.mail_ids') m
   WHERE m.value=q.id) END)"""


def marker(**fields):
    return json.dumps(
        {
            "format": MARKER_FORMAT,
            "events": {"first": 1, "last": 2},
            "mail_ids": ["keep"],
            **fields,
        }
    )


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE checkpoints (session_key TEXT, record_json TEXT);"
        "CREATE TABLE session_questions (id TEXT PRIMARY KEY, sender TEXT, recipient TEXT);"
    )
    db.executemany(
        "INSERT INTO session_questions VALUES (?,'sender','recipient')", [(i,) for i in IDS]
    )
    try:
        yield db
    finally:
        db.close()


def selected(conn, predicate):
    return {r[0] for r in conn.execute("SELECT q.id FROM session_questions q WHERE " + predicate)}


@pytest.mark.parametrize("scope", ["sender", "recipient", "unrelated"])
@pytest.mark.parametrize(
    "raw,protected",
    [
        (marker(), {"keep"}),
        (marker(mail_ids=[]), set()),
        (marker(mail_ids=["missing"]), set()),
        (marker(mail_ids=["keep", "keep"]), {"keep"}),
        (marker(mail_ids=['quote"\\id', "郵件🦆"]), {'quote"\\id', "郵件🦆"}),
        (marker(mail_ids=[None, {"id": "keep"}, ["keep"]]), set()),
        (marker(mail_ids=[1, True]), set()),
        (marker(mail_ids=None), IDS),
        (marker(mail_ids="keep"), IDS),
        (marker(mail_ids=17), IDS),
        (marker(mail_ids={"0": "keep"}), IDS),
        (json.dumps({"format": MARKER_FORMAT, "events": {"first": 1, "last": 2}}), IDS),
        (marker(events={"first": 1}), IDS),
        (marker(events={"first": "1", "last": 2}), IDS),
        (marker(events={"first": 1.0, "last": 2}), IDS),
        (marker(events=None), IDS),
        (marker(format="future-format"), IDS),
        (marker(format="checkpoint_fork_merge_v1"), set()),
        ('{"mail_ids":["keep"]}', set()),
        # Preserve existing SQL NULL comparison behavior, not a new policy.
        (marker(format=None), {"keep"}),
        ('{"format":', IDS),
        ("[]", IDS),
        ("null", IDS),
        ('"scalar"', IDS),
        (None, IDS),
    ],
)
def test_exact_candidate_ids_match_old_query_and_policy(conn, scope, raw, protected):
    conn.execute("INSERT INTO checkpoints VALUES (?,?)", (scope, raw))
    predicate = retention_predicate("mail")
    expected = IDS - protected if scope != "unrelated" else IDS
    assert selected(conn, predicate) == selected(conn, OLD)
    assert selected(conn, predicate) == expected


@pytest.mark.parametrize(
    "statement", ["SELECT q.id FROM session_questions q", "DELETE FROM session_questions AS q"]
)
def test_checkpoint_json_work_does_not_grow_with_old_mail(conn, statement):
    conn.execute(
        "INSERT INTO checkpoints VALUES('sender',?)",
        (marker(evidence="x" * 100_000),),
    )
    calls = 0
    with sqlite3.connect(":memory:") as json_db:

        def counted_valid(value):
            nonlocal calls
            calls += 1
            return json_db.execute("SELECT json_valid(?)", (value,)).fetchone()[0]

        conn.create_function("json_valid", 1, counted_valid)
        sql = statement + " WHERE " + retention_predicate("mail")
        conn.execute(sql).fetchall()
        small = calls
        calls = 0
        conn.executemany(
            "INSERT INTO session_questions VALUES(?,'sender','recipient')",
            [(f"old-{i}",) for i in range(500)],
        )
        conn.execute(sql).fetchall()
        assert 0 < calls == small <= 3
        if statement.startswith("DELETE"):
            assert {r[0] for r in conn.execute("SELECT id FROM session_questions")} == {"keep"}


def test_markers_only_protect_mail_in_their_sender_or_recipient_scope(conn):
    conn.execute("DELETE FROM session_questions")
    conn.executemany(
        "INSERT INTO session_questions VALUES (?,?,?)",
        [
            ("one", "a", "b"),
            ("two", "b", "a"),
            ("three", "c", "d"),
            ("four", "a", "c"),
        ],
    )
    conn.execute(
        "INSERT INTO checkpoints VALUES (?,?)",
        ("b", marker(mail_ids=["one", "two", "three", "four"])),
    )
    assert selected(conn, retention_predicate("mail")) == {"three", "four"}


def test_expiry_and_analytics_keep_pinned_mail_in_disposable_store(tmp_path):
    from duckterm.persistence.history import HistoryStore

    store = HistoryStore(tmp_path / "db.sqlite")
    try:
        for key in ("sender", "recipient"):
            store.record(
                {
                    "_id": key,
                    "_ts": 1,
                    "event_type": "SessionStart",
                    "session_key": key,
                    "test": True,
                }
            )
        for key in ("keep", "drop"):
            store._conn.execute(
                "INSERT INTO session_questions "
                "(id,sender,recipient,sender_name,root,question,status,created_at,expires_at,"
                "answered_at,idempotency_key,content_hash) "
                "VALUES (?,'sender','recipient','test','test','test','answered',1,2,2,?,'test')",
                (key, key),
            )
        store._conn.execute(
            "INSERT INTO checkpoints (id,session_key,label,created_at,record_json) "
            "VALUES ('pin','sender','test',1,?)",
            (marker(),),
        )
        store._conn.commit()
        store.session_api._sweep(force_retire=True)
        assert {r[0] for r in store._conn.execute("SELECT id FROM session_questions")} == {"keep"}
        assert store._conn.execute("SELECT count(*) FROM mail_rollup_v1").fetchone()[0] == 0
        store.session_api._sweep(force_retire=True)
        assert {r[0] for r in store._conn.execute("SELECT id FROM session_questions")} == {"keep"}
    finally:
        store.purge_test_sessions()
        store.close()
