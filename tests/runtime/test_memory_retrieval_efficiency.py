"""Retrieval avoids unrelated reads without relaxing source/grant validity."""

import asyncio
import hashlib
import json
import sqlite3
from copy import deepcopy

import pytest
from test_memory import memory_rig, transcript

from duckterm import memory_sources
from duckterm.core.session_api import APIError
from duckterm.memory import Memory

__all__ = ["memory_rig"]


def test_artifact_connections_close_even_on_corrupt_content(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    original = sqlite3.connect
    opened = []

    class Tracked(sqlite3.Connection):
        def close(self):
            self.was_closed = True
            super().close()

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs, factory=Tracked)
        conn.was_closed = False
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    catalog = {
        "session": "agent",
        "missing_identity": False,
        "sources": [
            {"id": str(i), "kind": "artifact", "artifact_id": "missing", "version": "0" * 64}
            for i in range(100)
        ],
    }
    available, unavailable = server.memory.materialize(catalog)
    assert not available and len(unavailable) == 100
    assert len(opened) == 100 and all(conn.was_closed for conn in opened)


def test_retained_native_cache_reuses_both_snapshots_and_detects_replacement(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "first decision")
    desc = {
        "id": memory_sources.identity("agent", "claude-code", "agent-claude"),
        "kind": "conversation",
        "runtime": "claude-code",
        "native_id": "agent-claude",
        "path": str(path),
    }
    first = memory_sources.read_native("agent", desc, retain=True)
    path.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "second decision"}})
        + "\n"
    )
    second = memory_sources.read_native("agent", desc, retain=True)
    original = memory_sources.read_native
    reads = []

    def read(*args, **kwargs):
        reads.append(args[1]["snapshot"])
        return original(*args, **kwargs)

    monkeypatch.setattr(memory_sources, "read_native", read)
    for _ in range(2):
        for source in (first, second):
            assert server.memory._read_native("agent", source)["version"] == source["version"]
    assert reads == [first["snapshot"], second["snapshot"]]
    saved = memory_sources.directory("agent") / (first["snapshot"] + ".jsonl")
    saved.write_bytes(saved.read_bytes().replace(b"first", b"other"))
    with pytest.raises(APIError, match="checksum"):
        server.memory._read_native("agent", first)


def test_exact_event_read_does_not_open_unrelated_native_history(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "large unrelated native history")
    catalog = server.memory.catalog("agent", retrieval=True)
    source = next(s for s in catalog["sources"] if s["kind"] == "event")

    def forbidden(*args, **kwargs):
        pytest.fail("exact event read opened an unrelated transcript")

    monkeypatch.setattr(memory_sources, "read_native", forbidden)
    result = asyncio.run(
        server.memory.operate("agent", "read", {"source": [source["id"] + ":" + source["version"]]})
    )
    assert source["records"][0]["text"] in result["text"]
    assert result["coverage"]["selection"] == "requested_source"
    assert result["coverage"]["complete"]


def test_new_sources_do_not_invalidate_captured_read_but_edits_removals_and_grants_do():
    old = {
        "sources": [{"id": "a", "version": "v1"}],
        "grant": {"root": "allowed"},
        "retained": {"old": "proof"},
    }
    new = deepcopy(old)
    new["sources"].append({"id": "b", "version": "v2"})
    assert Memory._catalog_valid(old, new)
    new["sources"][0]["version"] = "replacement"
    assert not Memory._catalog_valid(old, new)
    new = deepcopy(old)
    new["sources"] = []
    assert not Memory._catalog_valid(old, new)
    for field in ("grant", "retained"):
        new = deepcopy(old)
        new[field] = {}
        assert not Memory._catalog_valid(old, new)


def test_codex_metadata_is_validated_but_not_decoded_again_by_message_parser(tmp_path, monkeypatch):
    from duckterm.runtimes import codex

    path = tmp_path / "native.jsonl"
    meta = json.dumps({"type": "compacted", "payload": {"replacement_history": "x" * 10000}})
    msg = json.dumps(
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": "real message"},
        }
    )
    raw = (meta + "\n" + msg + "\n").encode()
    path.write_bytes(raw)
    original = codex._parse_message_lines
    seen = []

    def parse(lines, start):
        seen.append(start)
        return original(lines, start)

    monkeypatch.setattr(codex, "_parse_message_lines", parse)
    result = memory_sources.read_native(
        "probe",
        {"kind": "conversation", "runtime": "codex", "native_id": "probe", "path": str(path)},
    )
    assert seen == [1]
    assert result["records"][0]["text"] == "real message"
    assert result["version"] == hashlib.sha256(raw).hexdigest()
    assert result["records"][0]["id"] == "1"
    # A malformed metadata line is still rejected, not silently skipped.
    path.write_text('{"type":\n' + msg + "\n")
    with pytest.raises(APIError):
        memory_sources.read_native(
            "probe",
            {"kind": "conversation", "runtime": "codex", "native_id": "probe", "path": str(path)},
        )
