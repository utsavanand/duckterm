"""Converting raw copies kept before records-v1 keeps every citation readable."""

import hashlib
import json

import pytest
from test_memory_large_native import message, source

from duckterm import memory_convert, memory_sources
from duckterm.core.session_api import APIError
from duckterm.persistence.digests import DigestStore
from duckterm.persistence.history import HistoryStore

KEY = "probe"


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """Three raw versions of one growing conversation, as kept before records-v1.

    v1 is a pointer into v2's whole copy, v3 is another whole copy (never
    deduplicated), and one raw copy nothing cites.
    """
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    conn = HistoryStore(tmp_path / "state" / "db.sqlite")._conn
    DigestStore(conn=conn)
    root = memory_sources.directory(KEY)
    parts = [message("first decision"), message("second decision"), message("third decision")]
    snapshots = []
    for count in (1, 2, 3):
        raw = b"".join(parts[:count])
        snapshots.append(hashlib.sha256(raw).hexdigest())
        (root / (snapshots[-1] + ".jsonl")).write_bytes(raw)
    v1, v2, v3 = snapshots
    (root / (v1 + ".jsonl")).unlink()
    (root / (v1 + ".prefix")).write_text(json.dumps({"of": v2, "bytes": len(parts[0])}))
    orphan = root / (hashlib.sha256(b"x").hexdigest() + ".jsonl")
    orphan.write_bytes(message("nobody cites this"))
    ref = {**source(tmp_path / "gone.jsonl"), "id": "c" * 32, "cwd": ".", "root": ""}
    conn.execute(
        "INSERT INTO checkpoints (id, session_key, label, record_json, created_at) "
        "VALUES ('cp', ?, 'manual', ?, 1)",
        (KEY, json.dumps({"memory_source": {**ref, "snapshot": v3}})),
    )
    conn.execute(
        "INSERT INTO digest_items (id, session_key, bucket, text, created_at, updated_at) "
        "VALUES ('rev', ?, 'summary_revision_v1', ?, 1, 1)",
        (KEY, json.dumps({"memory_sources": [{**ref, "snapshot": v} for v in (v1, v2)]})),
    )
    conn.commit()
    return conn, root, ref, snapshots, orphan


def saved_refs(conn):
    found = []
    for _, _, _, value in memory_convert._rows(conn, KEY):
        memory_convert._walk(value, found)
    return found


def test_conversion_rebinds_every_citation_and_trashes_only_cited_raw(legacy, tmp_path):
    conn, root, ref, snapshots, orphan = legacy
    before = {
        v: memory_sources.read_native(KEY, {**ref, "snapshot": v})["records"] for v in snapshots
    }
    plan = memory_convert.plan(conn)
    assert plan["sessions"][0]["versions"] == 3 and plan["uncited_raw_files"] == 1

    report = memory_convert.convert(conn, KEY, tmp_path / "Trash")

    assert report["converted"] == 3 and not report["unreadable"]
    refs = saved_refs(conn)
    assert len(refs) == 3 and all(r["records_sha256"] for r in refs)
    for r in refs:
        assert memory_sources.read_native(KEY, r)["records"] == before[r["snapshot"]]
    # One whole copy for the growing conversation; the older versions point into it.
    v1, v2, v3 = snapshots
    assert [p.name for p in root.glob("*.records")] == [v3 + ".records"]
    assert {p.name for p in root.glob("*.records-prefix")} == {
        v1 + ".records-prefix",
        v2 + ".records-prefix",
    }
    assert not list(root.glob("*.prefix")) and list(root.glob("*.jsonl")) == [orphan]
    assert {p.name for p in (tmp_path / "Trash" / KEY).iterdir()} == {
        v1 + ".prefix",
        v2 + ".jsonl",
        v3 + ".jsonl",
    }
    assert memory_convert.pending(conn) == {}


def test_a_copy_that_reads_back_differently_changes_nothing(legacy, tmp_path, monkeypatch):
    conn, root, _, _, _ = legacy
    before = sorted(p.name for p in root.iterdir())
    original = memory_sources.read_native

    def altered(key, ref, **kwargs):
        value = original(key, ref, **kwargs)
        if "records_sha256" in ref:
            value = {**value, "records": value["records"][:-1]}
        return value

    monkeypatch.setattr(memory_sources, "read_native", altered)
    with pytest.raises(APIError, match="does not read back the same"):
        memory_convert.convert(conn, KEY, tmp_path / "Trash")
    assert not any("records_sha256" in r for r in saved_refs(conn))
    assert not (tmp_path / "Trash").exists()
    assert {p.name for p in root.iterdir()} >= set(before)


def test_a_raw_copy_an_unconverted_pointer_needs_stays(legacy, tmp_path):
    conn, root, _, snapshots, _ = legacy
    # A pointer nothing cites, into v3's raw copy, keeps that copy in place.
    stray = hashlib.sha256(b"stray").hexdigest()
    (root / (stray + ".prefix")).write_text(json.dumps({"of": snapshots[2], "bytes": 1}))
    memory_convert.convert(conn, KEY, tmp_path / "Trash")
    assert (root / (snapshots[2] + ".jsonl")).is_file()
    assert (root / (stray + ".prefix")).is_file()
