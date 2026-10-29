import json
from pathlib import Path

import pytest

from duckterm import context_evidence as evidence

STAMP = "2026-10-19T10:00:00.000Z"


def write(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def read(path, runtime="codex", identity="native"):
    return evidence._read(path, runtime, identity, evidence._signature(path))


def meta():
    return {"type": "session_meta", "payload": {"id": "native"}}


def compact(stamp=STAMP):
    return {"type": "compacted", "timestamp": stamp, "payload": {"window_id": "w"}}


def usage(capacity):
    return {
        "type": "event_msg",
        "payload": {"type": "token_count", "info": {"model_context_window": capacity}},
    }


def test_codex_uses_explicit_current_conversation_evidence(tmp_path):
    path = write(tmp_path / "log", [meta(), compact(), usage(258400)])
    before = path.read_bytes()
    result = read(path)
    assert result["context_window_tokens"] == 258400
    assert result["last_compaction_at"] == STAMP
    assert result["history_scope"] == "complete"
    assert path.read_bytes() == before
    assert read(path, identity="other")["reason"] == "identity_unverified"


def test_claude_completion_marker_not_usage_drop_or_model_lookup(tmp_path):
    path = write(
        tmp_path / "log",
        [
            {
                "sessionId": "native",
                "type": "system",
                "subtype": "compact_boundary",
                "uuid": "c",
                "timestamp": STAMP,
            },
            {
                "sessionId": "native",
                "type": "assistant",
                "message": {"model": "claude-opus-5", "usage": {"input_tokens": 1}},
            },
        ],
    )
    result = read(path, "claude-code")
    assert result["last_compaction_at"] == STAMP
    assert result["context_window_tokens"] is None
    write(
        path,
        [{"sessionId": "native", "type": "assistant", "message": {"usage": {"input_tokens": 1}}}],
    )
    assert read(path, "claude-code")["last_compaction_at"] is None


@pytest.mark.parametrize("bad", [None, 0, -1, True, "1000000", 2**53])
def test_invalid_latest_capacity_does_not_reuse_old_value(tmp_path, bad):
    path = write(tmp_path / "log", [meta(), usage(100)])
    assert read(path)["context_window_tokens"] == 100
    with path.open("a") as stream:
        stream.write(json.dumps(usage(bad)) + "\n")
    assert read(path)["context_window_tokens"] is None


def test_partial_history_does_not_claim_never_compacted(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "TAIL_BYTES", 140)
    path = write(tmp_path / "log", [meta(), compact(), {"padding": "x" * 500}, usage(258400)])
    result = read(path)
    assert result["history_scope"] == "recent"
    assert result["last_compaction_at"] is None
    assert result["context_window_tokens"] == 258400


def test_malformed_records_and_timestamp_are_not_evidence(tmp_path):
    path = write(tmp_path / "log", [meta(), compact()])
    assert read(path)["last_compaction_at"] == STAMP
    with path.open("a") as stream:
        stream.write(json.dumps(compact("not-a-date")) + "\n[]\nnull\nbroken JSON\n")
    assert read(path)["last_compaction_at"] is None


def test_missing_and_contested_bindings_never_search_old_transcripts(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not discover a transcript")

    monkeypatch.setattr(evidence, "runtime_for", forbidden)
    for state in ("pending", "missing", "contested"):
        assert (
            evidence.project({"runtime": "codex", "cwd": "/tmp"}, {"status": state})["reason"]
            == "identity_unavailable"
        )


def test_cache_invalidates_after_append_and_replacement(tmp_path):
    path = write(tmp_path / "log", [meta(), usage(100)])
    assert read(path)["context_window_tokens"] == 100
    with path.open("a") as stream:
        stream.write(json.dumps(usage(200)) + "\n")
    assert read(path)["context_window_tokens"] == 200
    replacement = write(tmp_path / "new", [meta(), usage(300)])
    replacement.replace(path)
    assert read(path)["context_window_tokens"] == 300


def test_current_binding_uses_exact_recorded_id(monkeypatch, tmp_path):
    path = write(tmp_path / "log", [meta(), usage(258400)])

    class Harness:
        def locate_transcript(self, *, cwd, session_id):
            assert cwd == Path("/tmp/project")
            assert session_id == "native"
            return path

    monkeypatch.setattr(evidence, "runtime_for", lambda *args: Harness())
    result = evidence.project(
        {"runtime": "codex", "cwd": "/tmp/project"}, {"status": "recorded", "native_id": "native"}
    )
    assert result["context_window_tokens"] == 258400


def codex_usage(used, cached=201856, total=23639388):
    row = usage(258400)
    row["payload"]["info"].update(
        last_token_usage={"input_tokens": used, "cached_input_tokens": cached},
        total_token_usage={"input_tokens": total},
    )
    return row


def test_codex_occupancy_is_latest_input_including_cache_not_lifetime_spend(tmp_path):
    path = write(
        tmp_path / "log",
        [
            meta(),
            {"type": "turn_context", "payload": {"model": "old-model"}},
            codex_usage(210000),
            {"type": "turn_context", "payload": {"model": "gpt-6-astra"}},
            codex_usage(202390),
        ],
    )
    result = read(path)
    assert result["context_tokens"] == 202390
    assert result["context_window_tokens"] == 258400
    assert result["model"] == "gpt-6-astra"
    assert result["context_tokens"] / result["context_window_tokens"] == pytest.approx(
        0.7832, abs=0.0001
    )


@pytest.mark.parametrize("bad", [None, -1, True, "202390", 1.5, 2**53])
def test_invalid_latest_usage_is_unknown_not_an_old_count(tmp_path, bad):
    path = write(tmp_path / "log", [meta(), codex_usage(202390), codex_usage(bad)])
    assert read(path)["context_tokens"] is None
    assert read(path)["context_window_tokens"] == 258400


def test_zero_is_valid_and_new_missing_usage_does_not_reuse_old_count(tmp_path):
    path = write(tmp_path / "log", [meta(), codex_usage(0)])
    assert read(path)["context_tokens"] == 0
    with path.open("a") as stream:
        stream.write(json.dumps(usage(258400)) + "\n")
    assert read(path)["context_tokens"] is None


@pytest.mark.parametrize("model", [None, "", "   ", 42, "m" * 257])
def test_invalid_latest_model_does_not_reuse_previous_turn_model(tmp_path, model):
    path = write(
        tmp_path / "log",
        [
            meta(),
            {"type": "turn_context", "payload": {"model": "old"}},
        ],
    )
    assert read(path)["model"] == "old"
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "turn_context", "payload": {"model": model}}) + "\n")
    assert read(path)["model"] is None


def test_unverified_identity_does_not_expose_model_or_usage(tmp_path):
    path = write(tmp_path / "log", [meta(), codex_usage(202390)])
    result = read(path, identity="different")
    assert result["context_tokens"] is None
    assert result["model"] is None
