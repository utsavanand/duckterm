"""Large provider metadata must not force full replay on every Messages poll."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from duckterm.runtimes.claude_code import _parse_message_lines as claude_lines
from duckterm.runtimes.codex import _parse_message_lines as codex_lines
from duckterm.runtimes.message_cache import MessageCache


def line(text: str, codex: bool = False) -> bytes:
    message = {"role": "user", "content": text}
    obj = (
        {"type": "response_item", "payload": {"type": "message", **message}}
        if codex
        else {"type": "user", "message": message}
    )
    return json.dumps(obj).encode() + b"\n"


def settled_clock(path, monkeypatch):
    stamp = path.stat()
    now = max(stamp.st_mtime_ns, stamp.st_ctime_ns) + 3_000_000_000
    monkeypatch.setattr(time, "time_ns", lambda: now)


@pytest.mark.parametrize("codex", [False, True])
def test_source_larger_than_budget_retains_small_messages(tmp_path, monkeypatch, codex):
    path = tmp_path / "large-source.jsonl"
    ignored = json.dumps({"type": "compacted", "data": "x" * 65536}).encode() + b"\n"
    path.write_bytes(ignored * 4 + line("visible", codex))
    settled_clock(path, monkeypatch)
    parser = codex_lines if codex else claude_lines
    cache = MessageCache(parser, max_bytes=32 * 1024)
    response = cache.response(path, "runtime", "native")
    assert json.loads(response)["messages"][0]["id"] == 4
    assert path.stat().st_size > cache._max_bytes
    assert cache._states[path].retained_bytes < cache._max_bytes
    with (
        patch.object(Path, "open", side_effect=AssertionError("replayed unchanged source")),
        ThreadPoolExecutor(max_workers=3) as pool,
    ):
        replies = list(pool.map(lambda _: cache.response(path, "runtime", "native"), range(3)))
    assert all(reply is response for reply in replies)
    with path.open("ab") as output:
        output.write(line("new message", codex))
    with patch.object(cache, "_parser", wraps=parser) as parse:
        updated = json.loads(cache.response(path, "runtime", "native"))["messages"]
    assert [row["id"] for row in updated] == [4, 5]
    assert parse.call_args_list[0].args[1] == 5
    assert updated[0] == json.loads(response)["messages"][0]


def test_parsed_containers_count_toward_budget(tmp_path):
    path = tmp_path / "many-small-records.jsonl"
    path.write_bytes(line("x") * 30)
    cache = MessageCache(claude_lines, max_bytes=path.stat().st_size * 2)
    assert len(cache.read(path)) == 30
    assert path not in cache._states


def test_response_bytes_count_toward_budget(tmp_path):
    path = tmp_path / "response.jsonl"
    path.write_bytes(line("hello " * 1000))
    cache = MessageCache(claude_lines)
    cache.read(path)
    cache._max_bytes = cache._states[path].retained_bytes + 100
    response = cache.response(path, "runtime", "native")
    assert json.loads(response)["messages"][0]["blocks"][0]["text"] == "hello " * 1000
    assert path not in cache._states


def test_overlapping_cold_reads_parse_large_source_once(tmp_path, monkeypatch):
    path = tmp_path / "concurrent.jsonl"
    ignored = json.dumps({"type": "compacted", "padding": "x" * 65536}).encode() + b"\n"
    path.write_bytes(ignored + line("visible", True))
    settled_clock(path, monkeypatch)
    cache = MessageCache(codex_lines, max_bytes=32 * 1024)
    with (
        patch.object(cache, "_parser", wraps=codex_lines) as parse,
        ThreadPoolExecutor(max_workers=3) as pool,
    ):
        replies = list(pool.map(lambda _: cache.response(path, "codex", "native"), range(3)))
    assert all(reply is replies[0] for reply in replies)
    assert len([call for call in parse.call_args_list if call.args[0]]) == 1
