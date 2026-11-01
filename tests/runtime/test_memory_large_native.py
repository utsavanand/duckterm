"""Large raw histories stay readable without relaxing text or integrity bounds."""

import hashlib
import json

import pytest

from duckterm import memory_sources
from duckterm.core.session_api import APIError
from duckterm.runtimes import codex


def message(text):
    return (
        json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": text},
            }
        ).encode()
        + b"\n"
    )


def source(path):
    return {"kind": "conversation", "runtime": "codex", "native_id": "fixture", "path": str(path)}


def test_history_larger_than_old_raw_limit_preserves_first_and_last_messages(tmp_path):
    path = tmp_path / "large.jsonl"
    first, last = message("Keep the original owner constraint."), message("Continue the new work.")
    metadata = (
        json.dumps(
            {"type": "compacted", "payload": {"replacement_history": "x" * (1024 * 1024)}}
        ).encode()
        + b"\n"
    )
    digest = hashlib.sha256()
    # Exercise both the old preflight and in-loop 512 MiB guards on a real file.
    # No large in-memory fixture and no generated test data left on disk.
    try:
        with path.open("wb") as stream:
            stream.write(first)
            digest.update(first)
            for _ in range(513):
                stream.write(metadata)
                digest.update(metadata)
            stream.write(last)
            digest.update(last)
        assert path.stat().st_size > 512 * 1024 * 1024
        result = memory_sources.read_native("probe", source(path))
        assert [r["text"] for r in result["records"]] == [
            "Keep the original owner constraint.",
            "Continue the new work.",
        ]
        assert [r["id"] for r in result["records"]] == ["0", "514"]
        assert result["version"] == digest.hexdigest()
        assert result["bytes"] == path.stat().st_size
    finally:
        path.unlink(missing_ok=True)


def test_retention_keeps_exact_raw_bytes_including_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    raw = message("old decision") + b'{"type":"compacted","payload":{}}\n' + message("new work")
    path.write_bytes(raw)
    saved = memory_sources.read_native("probe", source(path), retain=True)
    snapshot = memory_sources.directory("probe") / (saved["snapshot"] + ".jsonl")
    assert snapshot.read_bytes() == raw
    path.unlink()
    restored = memory_sources.read_native("probe", saved)
    assert restored["records"] == saved["records"]
    assert restored["version"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("limit", ["MAX_LINE_BYTES", "MAX_TEXT_BYTES"])
def test_allocation_limits_still_fail_without_retaining_partial_capture(
    tmp_path, monkeypatch, limit
):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(memory_sources, limit, 100)
    path = tmp_path / "native.jsonl"
    path.write_bytes(message("x" * 101))
    with pytest.raises(APIError, match="exceeds the current read bound"):
        memory_sources.read_native("probe", source(path), retain=True)
    assert not list(memory_sources.directory("probe").iterdir())


def test_scan_stops_at_captured_extent_and_refuses_concurrent_append(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    path.write_bytes(message("captured decision"))
    original = codex._parse_message_lines
    seen = []

    def append_during_parse(lines, start):
        seen.append(start)
        with path.open("ab") as stream:
            stream.write(message("concurrent new work"))
        return original(lines, start)

    monkeypatch.setattr(codex, "_parse_message_lines", append_during_parse)
    with pytest.raises(APIError, match="Conversation changed while reading"):
        memory_sources.read_native("probe", source(path), retain=True)
    assert seen == [0]
    assert not list(memory_sources.directory("probe").iterdir())


def test_an_appended_transcript_keeps_one_copy_and_every_version_reads_exactly(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    path.write_bytes(message("first decision"))
    v1 = memory_sources.read_native("probe", source(path), retain=True)
    with path.open("ab") as stream:
        stream.write(message("second decision"))
    v2 = memory_sources.read_native("probe", source(path), retain=True)
    with path.open("ab") as stream:
        stream.write(message("third decision"))
    v3 = memory_sources.read_native("probe", source(path), retain=True)
    root = memory_sources.directory("probe")
    # One whole copy; the older versions are pointers into it.
    assert sorted(p.name for p in root.glob("*.jsonl")) == [v3["snapshot"] + ".jsonl"]
    assert {p.stem for p in root.glob("*.prefix")} == {v1["snapshot"], v2["snapshot"]}
    path.unlink()
    for saved, texts in ((v1, ["first decision"]), (v2, ["first decision", "second decision"])):
        restored = memory_sources.read_native("probe", saved)
        assert [r["text"] for r in restored["records"]] == texts
        assert restored["version"] == saved["snapshot"]


def test_a_rewritten_transcript_keeps_both_whole_copies(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    path.write_bytes(message("original wording"))
    v1 = memory_sources.read_native("probe", source(path), retain=True)
    path.write_bytes(message("rewritten wording") + message("more"))
    v2 = memory_sources.read_native("probe", source(path), retain=True)
    root = memory_sources.directory("probe")
    assert {p.stem for p in root.glob("*.jsonl")} == {v1["snapshot"], v2["snapshot"]}
    assert not list(root.glob("*.prefix"))
    assert [r["text"] for r in memory_sources.read_native("probe", v1)["records"]] == [
        "original wording"
    ]
