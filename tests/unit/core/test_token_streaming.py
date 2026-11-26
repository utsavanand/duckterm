"""Token scans bound raw reads without changing accounting or tail handling."""

import io
import json
from pathlib import Path

import pytest
from tests.unit.core.test_tokens import NOW, claude, codex

from duckterm.core import tokens


@pytest.mark.parametrize("chunk_size", [1, 7, 64, 257, 1024 * 1024])
def test_chunk_boundaries_preserve_counts_crlf_long_lines_and_partial_tail(
    tmp_path, monkeypatch, chunk_size
):
    monkeypatch.setattr(tokens, "READ_CHUNK", chunk_size)
    path = tmp_path / "claude/a.jsonl"
    path.parent.mkdir()
    first = claude("one", "2026-09-25", 100, 5).encode()
    second = json.loads(claude("two", "2026-09-25", 40, 7))
    second["message"]["content"] = "🦆" * 300
    long_line = json.dumps(second, ensure_ascii=False).encode()
    pending = claude("three", "2026-09-25", 20, 3).encode()
    # CR-only records and a CRLF split across chunks keep byte splitlines behavior.
    complete = first + b"\r" + first + b"\r\ninvalid\n[]\n" + long_line + b"\n"
    path.write_bytes(complete + pending[:40])
    ledger = tokens.TokenLedger(path.parent, tmp_path / "codex")
    assert ledger.totals(7, NOW)["claude-code"] == dict(
        input=2, cache_read=140, cache_write=0, output=12
    )
    assert ledger._files[path].offset == len(complete)
    assert ledger.totals(7, NOW)["claude-code"]["output"] == 12
    with path.open("ab") as out:
        out.write(pending[40:] + b"\n")
    assert ledger.totals(7, NOW)["claude-code"]["output"] == 15
    # A truncation resets deduplication and totals, not just the read position.
    path.write_bytes(first + b"\n")
    assert ledger.totals(7, NOW)["claude-code"]["output"] == 5


@pytest.mark.parametrize("chunk_size", [1, 13, 128])
def test_codex_running_totals_and_model_switch_survive_chunked_appends(
    tmp_path, monkeypatch, chunk_size
):
    monkeypatch.setattr(tokens, "READ_CHUNK", chunk_size)
    root = tmp_path / "codex"
    root.mkdir()
    path = root / "rollout-test.jsonl"
    path.write_text(
        json.dumps(dict(type="session_meta", payload=dict(id="native")))
        + "\n"
        + json.dumps(dict(type="turn_context", payload=dict(model="first")))
        + "\n"
        + codex("2026-09-25", 100, 20, 5)
        + "\n"
    )
    ledger = tokens.TokenLedger(tmp_path / "claude", root)
    assert ledger.totals(7, NOW)["codex"] == dict(input=80, cache_read=20, cache_write=0, output=5)
    tail = codex("2026-09-25", 150, 30, 9)
    with path.open("a") as out:
        out.write(
            json.dumps(dict(type="turn_context", payload=dict(model="second"))) + "\n" + tail[:30]
        )
    assert ledger.totals(7, NOW)["codex"]["output"] == 5
    with path.open("a") as out:
        out.write(tail[30:] + "\n")
    rows = ledger.analytics(7, NOW, [])["rows"]
    assert {r["model"]: r["input"] for r in rows} == {"first": 80, "second": 40}
    assert ledger._files[path].native_id == "native"
    assert ledger.totals(7, NOW)["codex"]["output"] == 9


def test_reader_never_requests_the_whole_large_suffix(tmp_path, monkeypatch):
    path = tmp_path / "a.jsonl"
    payload = (claude("duplicate", "2026-09-25", 0, 1) + "\n").encode() * 100
    path.write_bytes(payload)
    monkeypatch.setattr(tokens, "READ_CHUNK", 127)
    requests = []

    class Reader(io.BytesIO):
        def read(self, size=-1):
            requests.append(size)
            assert 0 < size <= 127
            return super().read(size)

    real_open = Path.open

    def open_file(self, *args, **kwargs):
        return (
            Reader(payload)
            if self == path and args == ("rb",)
            else real_open(self, *args, **kwargs)
        )

    monkeypatch.setattr(Path, "open", open_file)
    ledger = tokens.TokenLedger(tmp_path, tmp_path / "none")
    assert ledger.totals(7, NOW)["claude-code"]["output"] == 1
    assert len(requests) > 1
