"""Cache eviction frees old records before a new transcript is parsed."""

import json
import weakref
from pathlib import Path

import pytest

from duckterm.runtimes.claude_code import _parse_message_lines
from duckterm.runtimes.message_cache import MessageCache


def record(text: str) -> bytes:
    return (
        json.dumps({"type": "user", "message": {"role": "user", "content": text}}).encode() + b"\n"
    )


@pytest.mark.parametrize("limit", ["files", "bytes"])
def test_lru_records_are_freed_before_the_new_parser_runs(tmp_path: Path, limit: str) -> None:
    first, second, incoming = (tmp_path / name for name in ("first", "second", "incoming"))
    for path in (first, second, incoming):
        path.write_bytes(record("message"))
    cache = MessageCache(
        _parse_message_lines,
        max_files=2 if limit == "files" else 8,
    )
    expected = cache.read(first)
    assert expected == [{"id": 0, "role": "user", "blocks": [{"type": "text", "text": "message"}]}]
    cache.read(second)
    if limit == "bytes":
        cache._max_bytes = sum(state.retained_bytes for state in cache._states.values())
    oldest = weakref.ref(cache._states[first])
    newer = weakref.ref(cache._states[second])

    def checked_parser(lines, start):
        assert oldest() is None, "evicted records still alive while allocating the next transcript"
        assert newer() is cache._states[second]
        return _parse_message_lines(lines, start)

    cache._parser = checked_parser
    assert cache.read(incoming) == expected
    assert list(cache._states) == [second, incoming]
    cache._parser = _parse_message_lines
    assert cache.read(first) == expected
    assert list(cache._states) == [incoming, first]


def test_growing_current_file_keeps_its_prefix_and_evicts_other_records(tmp_path: Path) -> None:
    current, other = tmp_path / "current", tmp_path / "other"
    content = record("message")
    current.write_bytes(content)
    other.write_bytes(content)
    cache = MessageCache(_parse_message_lines)
    cache.read(current)
    cache.read(other)
    cache._max_bytes = sum(state.retained_bytes for state in cache._states.values())
    evicted = weakref.ref(cache._states[other])
    current.write_bytes(content * 2)
    starts = []

    def checked_parser(lines, start):
        assert evicted() is None
        starts.append(start)
        return _parse_message_lines(lines, start)

    cache._parser = checked_parser
    rows = cache.read(current)
    assert [row["id"] for row in rows] == [0, 1]
    assert starts[0] == 1
    assert list(cache._states) == [current]
