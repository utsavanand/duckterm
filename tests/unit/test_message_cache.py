"""Transcript reads stay incremental without changing message identity."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duckterm.runtimes.claude_code import _parse_message_lines as claude_lines
from duckterm.runtimes.codex import _parse_message_lines as codex_lines
from duckterm.runtimes.message_cache import MessageCache


def line(text: str, codex: bool = False) -> bytes:
    obj = (
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}}
        if codex
        else {"type": "user", "message": {"role": "user", "content": text}}
    )
    return json.dumps(obj, ensure_ascii=False).encode() + b"\n"


@pytest.mark.parametrize("codex", [False, True])
def test_unchanged_file_not_opened_and_append_only_parses_new_lines(
    tmp_path: Path, codex: bool
) -> None:
    parser = codex_lines if codex else claude_lines
    cache = MessageCache(parser)
    path = tmp_path / "transcript.jsonl"
    first = line("first", codex)
    path.write_bytes(first)
    result = cache.read(path)
    assert result[0]["id"] == 0
    result[0]["message_key"] = "server-specific"
    with patch.object(Path, "open", side_effect=AssertionError("unchanged file reread")):
        assert cache.read(path) == parser([first.decode()], 0)
    with path.open("ab") as output:
        output.write(line("second", codex))
    with patch.object(cache, "_parser", wraps=parser) as parse:
        records = cache.read(path)
    assert [record["id"] for record in records] == [0, 1]
    assert parse.call_args_list[0].args[1] == 1
    assert len(parse.call_args_list[0].args[0]) == 1
    assert "first" not in str(parse.call_args_list)


@pytest.mark.parametrize("codex", [False, True])
def test_partial_unicode_and_final_record_do_not_duplicate(tmp_path: Path, codex: bool) -> None:
    parser = codex_lines if codex else claude_lines
    cache = MessageCache(parser)
    path = tmp_path / "transcript.jsonl"
    data = line("snow ☃", codex)
    split = data.index("☃".encode()) + 1
    path.write_bytes(data[:split])
    assert cache.read(path) == []
    with path.open("ab") as output:
        output.write(data[split:-1])
    assert cache.read(path) == parser([data.decode()], 0)
    with path.open("ab") as output:
        output.write(b"\n\nnot json\n" + line("last", codex))
    assert cache.read(path) == parser(path.read_text().splitlines(), 0)
    assert [row["id"] for row in cache.read(path)] == [0, 3]


def test_rewrite_replacement_truncation_and_deletion(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    cache = MessageCache(claude_lines)
    for text in ("old", "new", "longer text"):
        replacement = tmp_path / "replacement"
        replacement.write_bytes(line(text))
        replacement.replace(path)
        assert cache.read(path)[0]["blocks"] == [{"type": "text", "text": text}]
    path.write_bytes(line("short"))
    assert cache.read(path)[0]["blocks"] == [{"type": "text", "text": "short"}]
    path.write_bytes(line("other"))
    assert cache.read(path)[0]["blocks"] == [{"type": "text", "text": "other"}]
    path.unlink()
    assert cache.read(path) == []


def test_lru_eviction_reloads_instead_of_returning_other_session(tmp_path: Path) -> None:
    cache = MessageCache(claude_lines, max_files=1)
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(line("A"))
    b.write_bytes(line("B"))
    cache.read(a)
    cache.read(b)
    with patch.object(cache, "_parser", wraps=claude_lines) as parse:
        assert cache.read(a)[0]["blocks"] == [{"type": "text", "text": "A"}]
    assert parse.call_args_list[0].args[1] == 0


def test_thirty_mb_unchanged_transcript_does_no_io_or_parse(tmp_path: Path) -> None:
    path = tmp_path / "large.jsonl"
    # Large non-message records occur in real transcripts too. No real user data.
    ignored = json.dumps({"type": "progress", "padding": "x" * 65536}).encode() + b"\n"
    path.write_bytes(ignored * 480 + line("visible"))
    assert path.stat().st_size >= 30 * 1024 * 1024
    cache = MessageCache(claude_lines)
    expected = cache.read(path)
    assert expected[0]["id"] == 480
    with (
        patch.object(Path, "open", side_effect=AssertionError("reread")),
        patch.object(cache, "_parser", side_effect=AssertionError("reparse")),
    ):
        assert cache.read(path) == expected


@pytest.mark.parametrize("codex", [False, True])
def test_new_runtime_instances_share_cache(tmp_path: Path, codex: bool) -> None:
    from duckterm.runtimes.claude_code import ClaudeCodeRuntime
    from duckterm.runtimes.codex import CodexRuntime

    runtime = CodexRuntime if codex else ClaudeCodeRuntime
    path = tmp_path / "shared.jsonl"
    path.write_bytes(line("persist across requests", codex))
    with patch.object(runtime, "locate_transcript", return_value=path):
        first = runtime().messages(cwd=tmp_path, session_id="native")
        with patch.object(Path, "open", side_effect=AssertionError("fresh adapter reread")):
            assert runtime().messages(cwd=tmp_path, session_id="native") == first
