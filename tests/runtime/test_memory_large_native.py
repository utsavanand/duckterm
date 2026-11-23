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


def test_retention_keeps_records_without_native_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    raw = message("old decision") + b'{"type":"compacted","payload":{}}\n' + message("new work")
    path.write_bytes(raw)
    saved = memory_sources.read_native("probe", source(path), retain=True)
    root = memory_sources.directory("probe")
    assert not list(root.glob("*.jsonl"))
    kept = (root / (saved["snapshot"] + ".records")).read_bytes()
    assert b"compacted" not in kept and b"payload" not in kept
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
    assert sorted(p.name for p in root.glob("*.records")) == [v3["snapshot"] + ".records"]
    pointers = list(root.glob("*.records-prefix"))
    assert {p.name.split(".")[0] for p in pointers} == {v1["snapshot"], v2["snapshot"]}
    # Every older version points straight at the whole copy: one hop, not a chain.
    assert {json.loads(p.read_text())["of"] for p in pointers} == {v3["snapshot"]}
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
    assert {p.stem for p in root.glob("*.records")} == {v1["snapshot"], v2["snapshot"]}
    assert not list(root.glob("*.records-prefix"))
    assert [r["text"] for r in memory_sources.read_native("probe", v1)["records"]] == [
        "original wording"
    ]


def test_a_raw_copy_from_before_records_still_reads_and_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    raw = message("legacy decision")
    path.write_bytes(raw)
    current = memory_sources.read_native("probe", source(path))
    legacy = memory_sources.directory("probe") / (current["version"] + ".jsonl")
    legacy.write_bytes(raw)
    with path.open("ab") as stream:
        stream.write(message("later work"))
    memory_sources.read_native("probe", source(path), retain=True)
    path.unlink()
    restored = memory_sources.read_native("probe", {**source(path), "snapshot": current["version"]})
    assert [r["text"] for r in restored["records"]] == ["legacy decision"]
    assert legacy.read_bytes() == raw


def test_an_old_reference_keeps_reading_its_raw_copy_after_the_version_is_saved_again(
    tmp_path, monkeypatch
):
    # An idle agent's next checkpoint saves the same version in the new format.
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    raw = message("legacy decision")
    path.write_bytes(raw)
    current = memory_sources.read_native("probe", source(path))
    legacy = memory_sources.directory("probe") / (current["version"] + ".jsonl")
    legacy.write_bytes(raw)
    old_ref = {**source(path), "snapshot": current["version"]}
    saved = memory_sources.read_native("probe", source(path), retain=True)
    assert saved["snapshot"] == current["version"]
    path.unlink()
    for ref in (old_ref, saved):
        assert [r["text"] for r in memory_sources.read_native("probe", ref)["records"]] == [
            "legacy decision"
        ]


def test_a_tampered_pointer_or_copy_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    path.write_bytes(message("first decision"))
    v1 = memory_sources.read_native("probe", source(path), retain=True)
    with path.open("ab") as stream:
        stream.write(message("second decision"))
    memory_sources.read_native("probe", source(path), retain=True)
    root = memory_sources.directory("probe")
    pointer = root / (v1["snapshot"] + ".records-prefix")
    value = json.loads(pointer.read_text())
    pointer.write_text(json.dumps({**value, "records": 2}))
    with pytest.raises(APIError, match="checksum"):
        memory_sources.read_native("probe", v1)


def rewrite_records(path, edit):
    """Rewrite a records copy and recompute its own checksum, as an attacker could."""
    header, *lines = path.read_bytes().splitlines(keepends=True)
    lines = edit(lines)
    meta = {
        **json.loads(header),
        "records": len(lines),
        "sha256": hashlib.sha256(b"".join(lines)).hexdigest(),
    }
    path.write_bytes((json.dumps(meta) + "\n").encode() + b"".join(lines))
    return meta["sha256"]


def test_a_rewritten_copy_with_a_recomputed_checksum_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    path.write_bytes(message("Owner said: never push without approval"))
    saved = memory_sources.read_native("probe", source(path), retain=True)
    kept = memory_sources.directory("probe") / (saved["snapshot"] + ".records")
    rewrite_records(
        kept, lambda lines: [lines[0].replace(b"never push without approval", b"ALWAYS push")]
    )
    with pytest.raises(APIError, match="checksum"):
        memory_sources.read_native("probe", saved)
    # A reference without the digest only reads a raw copy, and there is none.
    with pytest.raises(APIError, match="unavailable"):
        memory_sources.read_native(
            "probe", {k: v for k, v in saved.items() if k != "records_sha256"}
        )


@pytest.mark.parametrize(
    "line", [b'{"id":"0","role":"user"}\n', b"[1]\n", b"not json\n"], ids=["no-text", "list", "bad"]
)
def test_a_malformed_record_is_refused_not_a_crash(tmp_path, monkeypatch, line):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    path = tmp_path / "native.jsonl"
    path.write_bytes(message("decision"))
    saved = memory_sources.read_native("probe", source(path), retain=True)
    kept = memory_sources.directory("probe") / (saved["snapshot"] + ".records")
    bound = rewrite_records(kept, lambda lines: [line])
    with pytest.raises(APIError, match="unreadable record"):
        memory_sources.read_native("probe", {**saved, "records_sha256": bound})


def compacted(size, **header):
    body = json.dumps(
        {
            "timestamp": "2026-11-17T00:00:00Z",
            **header,
            "type": "compacted",
            "payload": {"replacement_history": "x" * size},
        }
    )
    return body.encode() + b"\n"


@pytest.mark.parametrize("header", [{}, {"ordinal": 878}])
def test_an_oversized_codex_compaction_line_is_hashed_but_not_parsed(tmp_path, monkeypatch, header):
    # A 1.5 GB ui-dev history had 23 compaction lines of 16-23 MB (2026-11-17).
    monkeypatch.setattr(memory_sources, "MAX_LINE_BYTES", 4096)
    path = tmp_path / "rollout.jsonl"
    raw = (
        message("Before the compaction")
        + compacted(20_000, **header)
        + message("After the compaction")
    )
    path.write_bytes(raw)
    read = memory_sources.read_native("probe", source(path))
    assert [r["text"] for r in read["records"]] == ["Before the compaction", "After the compaction"]
    assert read["version"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    ("runtime", "line"),
    [
        ("codex", message("y" * 8000)),
        ("claude-code", compacted(20_000)),
        # Only the record's own type counts, not one nested in its content.
        (
            "codex",
            json.dumps(
                {
                    "timestamp": "2026-11-17T00:00:00Z",
                    "type": "response_item",
                    "payload": {"type": "compacted", "text": "y" * 8000},
                }
            ).encode()
            + b"\n",
        ),
    ],
    ids=["codex-message", "claude-compaction", "codex-nested-type"],
)
def test_other_oversized_lines_are_still_refused(tmp_path, monkeypatch, runtime, line):
    monkeypatch.setattr(memory_sources, "MAX_LINE_BYTES", 4096)
    path = tmp_path / "native.jsonl"
    path.write_bytes(line)
    with pytest.raises(APIError, match="exceeds the current read bound"):
        memory_sources.read_native("probe", {**source(path), "runtime": runtime})


@pytest.mark.parametrize(
    ("runtime", "line"),
    [
        (
            "codex",
            {
                "timestamp": "2026-11-15T10:00:00.250Z",
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": "Ship it."},
            },
        ),
        (
            "claude-code",
            {
                "timestamp": "2026-11-15T10:00:00.250Z",
                "type": "user",
                "uuid": "u1",
                "message": {"role": "user", "content": "Ship it."},
            },
        ),
    ],
)
def test_records_keep_when_the_harness_wrote_them(tmp_path, runtime, line):
    # Automatic checkpoints skip messages written while they were switched off.
    path = tmp_path / "native.jsonl"
    untimed = {k: v for k, v in line.items() if k != "timestamp"}
    path.write_text(json.dumps(line) + "\n" + json.dumps({**untimed, "uuid": "u2"}) + "\n")
    read = memory_sources.read_native("probe", {**source(path), "runtime": runtime})
    assert [r["at"] for r in read["records"]] == [1794736800250, None]
