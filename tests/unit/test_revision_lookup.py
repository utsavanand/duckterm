"""Immutable revision lookup must not repeatedly parse unrelated large bodies."""

import json
import sqlite3

from duckterm.persistence.digests import DigestStore


def value(key):
    return {
        "input_key": key,
        "summary": "Saved work",
        "source": {},
        "conversation": {},
        "summary_validation": {"ready": True},
        "retained_sources": ["evidence " * 10000],
    }


def save(store, key, now=1):
    store._conn.execute("BEGIN")
    identity = store.add_revision("agent", value(key), now)
    store._conn.commit()
    return identity


def test_warm_lookup_parses_only_new_revision_keys(tmp_path):
    store = DigestStore(tmp_path / "db.sqlite")
    for n in range(10):
        save(store, f"span:{n}")
    assert store.find_revision("agent", "span:3")["input_key"] == "span:3"
    traces = []
    store._conn.set_trace_callback(traces.append)
    assert store.find_revision("agent", "missing") is None
    assert store.find_revision("agent", "span:8")["input_key"] == "span:8"
    assert not any("json_valid" in sql for sql in traces)
    newest = save(store, "span:3", 2)
    traces.clear()
    assert store.find_revision("agent", "span:3")["id"] == newest
    assert sum("json_valid" in sql for sql in traces) == 1
    assert store.find_revision("other", "span:3") is None
    store.close()


def test_lookup_invalidates_external_edits_without_new_timestamp(tmp_path):
    path = tmp_path / "db.sqlite"
    store = DigestStore(path)
    identity = save(store, "before")
    assert store.find_revision("agent", "before")
    external = sqlite3.connect(path)
    with external:
        changed = {**value("after"), "format": "summary_revision_v1"}
        external.execute(
            "UPDATE digest_items SET text=? WHERE id=?", (json.dumps(changed), identity)
        )
    assert store.find_revision("agent", "before") is None
    assert store.find_revision("agent", "after")["id"] == identity
    external.close()
    store.close()


def test_uncommitted_keys_do_not_survive_rollback_or_deletion(tmp_path):
    store = DigestStore(tmp_path / "db.sqlite")
    original = save(store, "committed")
    assert store.find_revision("agent", "committed")["id"] == original
    store._conn.execute("BEGIN")
    pending = store.add_revision("agent", value("pending"), 2)
    assert store.find_revision("agent", "pending")["id"] == pending
    store._conn.rollback()
    assert store.find_revision("agent", "pending") is None
    assert store.find_revision("agent", "committed")["id"] == original
    store.delete_session("agent")
    assert store.find_revision("agent", "committed") is None
    store.close()


def test_progress_merge_never_normalizes_revision_bodies(tmp_path, monkeypatch):
    from duckterm.persistence import digests

    store = DigestStore(tmp_path / "db.sqlite")
    save(store, "span:1")
    original = digests._normalize

    def small_text(text):
        assert "retained_sources" not in text
        return original(text)

    monkeypatch.setattr(digests, "_normalize", small_text)
    result = store.merge("agent", [{"bucket": "learnings", "text": "Keep originals"}], [], 3)
    assert result["inserted"] == 1
    store.close()
