"""Converting raw copies kept before records-v1 keeps every citation readable."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest
from test_memory_large_native import message, source

from duckterm import cli, memory_convert, memory_sources
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

    report = memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)

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
        memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)
    assert not any("records_sha256" in r for r in saved_refs(conn))
    assert not [p for p in (tmp_path / "Trash").rglob("*") if p.is_file()]
    assert {p.name for p in root.iterdir()} >= set(before)


def test_a_raw_copy_an_unconverted_pointer_needs_stays(legacy, tmp_path):
    conn, root, _, snapshots, _ = legacy
    # A pointer nothing cites, into v3's raw copy, keeps that copy in place.
    stray = hashlib.sha256(b"stray").hexdigest()
    (root / (stray + ".prefix")).write_text(json.dumps({"of": snapshots[2], "bytes": 1}))
    memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)
    assert (root / (snapshots[2] + ".jsonl")).is_file()
    assert (root / (stray + ".prefix")).is_file()


def interrupt(monkeypatch, name, after):
    """Make os.<name> fail after `after` successful calls, as a crash would."""
    original = getattr(memory_convert.os, name)
    calls = []

    def fail(*args, **kwargs):
        if "dir_fd" not in kwargs and "src_dir_fd" not in kwargs:
            return original(*args, **kwargs)  # not the Trash move
        if len(calls) == after:
            raise OSError("interrupted")
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(memory_convert.os, name, fail)
    return lambda: monkeypatch.setattr(memory_convert.os, name, original)


@pytest.mark.parametrize(
    "call, after",
    [("link", 0), ("link", 1), ("unlink", 0)],
    ids=["before-first-move", "between-moves", "linked-not-unlinked"],
)
def test_a_run_interrupted_while_trashing_is_finished_by_the_next(
    legacy, tmp_path, monkeypatch, call, after
):
    conn, root, ref, snapshots, orphan = legacy
    before = {
        v: memory_sources.read_native(KEY, {**ref, "snapshot": v})["records"] for v in snapshots
    }
    restore = interrupt(monkeypatch, call, after)
    with pytest.raises(OSError, match="interrupted"):
        memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)
    restore()
    assert all(r["records_sha256"] for r in saved_refs(conn))
    left = memory_convert.pending(conn)[KEY]
    assert left and all(left.values())
    assert memory_convert.plan(conn)["raw_bytes"] > 0

    report = memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)

    assert report["converted"] == 0 and report["resumed"] == len(left)
    assert not report["unreadable"]
    assert not list(root.glob("*.prefix")) and list(root.glob("*.jsonl")) == [orphan]
    assert len(list((tmp_path / "Trash" / KEY).iterdir())) == 3
    for r in saved_refs(conn):
        assert memory_sources.read_native(KEY, r)["records"] == before[r["snapshot"]]
    assert memory_convert.pending(conn) == {}


def test_an_interrupted_run_still_updates_the_index(legacy, tmp_path, monkeypatch):
    conn, root, _, snapshots, _ = legacy
    (root / "latest.json").write_text(
        json.dumps({"codex:fixture": {"digest": snapshots[2], "bytes": 1}})
    )
    original = memory_convert.tempfile.mkstemp

    def crash_on_index(**kwargs):
        if kwargs.get("prefix") == ".latest-":
            raise OSError("crash")
        return original(**kwargs)

    monkeypatch.setattr(memory_convert.tempfile, "mkstemp", crash_on_index)
    with pytest.raises(OSError):
        memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)
    monkeypatch.setattr(memory_convert.tempfile, "mkstemp", original)
    memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)
    entry = json.loads((root / "latest.json").read_text())["codex:fixture"]
    assert entry["format"] == memory_sources.RECORDS_FORMAT and entry["records"] == 3


def test_a_resumed_raw_copy_that_changed_is_not_trashed(legacy, tmp_path, monkeypatch):
    conn, root, _, snapshots, _ = legacy
    restore = interrupt(monkeypatch, "link", 0)
    with pytest.raises(OSError):
        memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)
    restore()
    raw = root / (snapshots[2] + ".jsonl")
    raw.write_bytes(raw.read_bytes().replace(b"third", b"fifth"))
    report = memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)
    assert [u["snapshot"] for u in report["unreadable"]] == [snapshots[2]]
    assert raw.is_file() and not (tmp_path / "Trash" / KEY / raw.name).exists()


def test_a_trash_folder_that_is_a_symlink_is_refused(legacy, tmp_path):
    conn, root, _, snapshots, _ = legacy
    external = tmp_path / "external"
    external.mkdir()
    victim = external / (snapshots[2] + ".jsonl")
    victim.write_bytes(b"KEEP")
    trash = tmp_path / "Trash"
    trash.mkdir()
    (trash / KEY).symlink_to(external, target_is_directory=True)
    with pytest.raises(OSError):
        memory_convert.convert(conn, KEY, trash, tmp_path)
    assert victim.read_bytes() == b"KEEP"
    assert (root / (snapshots[2] + ".jsonl")).is_file()


def test_a_symlinked_folder_above_the_trash_is_refused(legacy, tmp_path):
    # As a symlinked ~/.Trash would be: only the last component was checked.
    conn, root, _, snapshots, _ = legacy
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / "alias").symlink_to(external, target_is_directory=True)
    with pytest.raises(OSError):
        memory_convert.convert(conn, KEY, tmp_path / "alias" / "dated-Trash", tmp_path)
    assert not list(external.rglob("*"))
    assert (root / (snapshots[2] + ".jsonl")).is_file()
    # Refused before anything changed, not after the references were rebound.
    assert not any("records_sha256" in r for r in saved_refs(conn))


def test_a_trash_outside_the_anchor_is_refused(legacy, tmp_path):
    conn, root, _, snapshots, _ = legacy
    with pytest.raises(APIError, match="must be inside"):
        memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path / "home")
    assert (root / (snapshots[2] + ".jsonl")).is_file()


def test_a_file_already_in_the_trash_is_not_replaced(legacy, tmp_path):
    conn, root, _, snapshots, _ = legacy
    existing = tmp_path / "Trash" / KEY / (snapshots[2] + ".jsonl")
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"KEEP")
    with pytest.raises(APIError, match="nothing replaced"):
        memory_convert.convert(conn, KEY, tmp_path / "Trash", tmp_path)
    assert existing.read_bytes() == b"KEEP"
    assert (root / (snapshots[2] + ".jsonl")).is_file()


def test_a_dry_run_creates_no_folders_and_reports_a_bad_session(legacy):
    conn, root, ref, _, _ = legacy
    for key in ("elsewhere", "bad/key"):
        conn.execute(
            "INSERT INTO checkpoints (id, session_key, label, record_json, created_at) "
            "VALUES (?, ?, 'manual', ?, 1)",
            (key, key, json.dumps({"memory_source": {**ref, "snapshot": "d" * 64}})),
        )
    conn.commit()
    plan = memory_convert.plan(conn)
    assert not (root.parent.parent / "elsewhere").exists()
    assert [s["session"] for s in plan["sessions"] if "error" in s] == ["bad/key"]
    with pytest.raises(APIError):
        memory_convert.convert(conn, "../outside", root.parent.parent / "Trash")


def test_the_cli_dry_run_does_not_set_up_the_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    with closing(sqlite3.connect(tmp_path / "db.sqlite")) as conn:
        conn.execute("CREATE TABLE checkpoints (session_key TEXT, record_json TEXT)")
        conn.execute("CREATE TABLE digest_items (session_key TEXT, text TEXT)")
    before = (tmp_path / "db.sqlite").read_bytes()
    assert cli._convert_memory_copies(False) == 0
    assert (tmp_path / "db.sqlite").read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["db.sqlite"]
