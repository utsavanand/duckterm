"""Streaming reads keep the legacy line identities while buffering only by chunk."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from duckterm.runtimes import message_cache
from duckterm.runtimes.claude_code import _parse_message_lines as claude_lines
from duckterm.runtimes.codex import _parse_message_lines as codex_lines
from duckterm.runtimes.message_cache import MessageCache

DEFAULT_CHUNK = getattr(message_cache, "READ_CHUNK", 1 << 20)
CHUNKS = [1, 3, 7, 64, DEFAULT_CHUNK]


def line(text: str, codex: bool = False) -> bytes:
    obj = (
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}}
        if codex
        else {"type": "user", "message": {"role": "user", "content": text}}
    )
    return json.dumps(obj, ensure_ascii=False).encode() + b"\n"


def awkward(codex: bool) -> bytes:
    """Every separator str.splitlines honours, plus bytes UTF-8 decoding replaces."""
    return b"".join(
        [
            line("a\u2028b", codex),  # Node leaves U+2028/2029 unescaped
            line("c\u2029d", codex),
            line("next\x85line", codex),
            line("lone\rreturn", codex),
            line("crlf", codex)[:-1] + b"\r\n",
            b"\n",
            line("vt\x0bff\x0cfs\x1cgs\x1drs\x1e", codex),
            # JSON escapes control characters inside strings. Exercise actual
            # separators between records too, where legacy splitlines counts them.
            line("raw CR", codex)[:-1] + b"\r" + line("after CR", codex),
            b"\x0b\x0c\x1c\x1d\x1e\n",
            line("bad", codex).replace(b"bad", b"bad\xff\xfe"),
            b'{"broken": "\xe2\x98\n',  # truncated multibyte right before the newline
            line("emoji \U0001f986", codex),
            line("last", codex)[:-1],  # valid final record without a newline
        ]
    )


def legacy(data: bytes, parser) -> list[dict[str, object]]:
    return parser(data.decode(errors="replace").splitlines(), 0)


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("codex", [False, True])
def test_whole_file_matches_legacy_split(tmp_path, monkeypatch, codex, chunk) -> None:
    monkeypatch.setattr(message_cache, "READ_CHUNK", chunk, raising=False)
    parser = codex_lines if codex else claude_lines
    data = awkward(codex)
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(data)
    expected = legacy(data, parser)
    assert expected[-1]["blocks"] == [{"type": "text", "text": "last"}]
    assert MessageCache(parser).read(path) == expected


@pytest.mark.parametrize("chunk", [1, 7, DEFAULT_CHUNK])
@pytest.mark.parametrize("codex", [False, True])
def test_appends_at_every_byte_keep_ids_stable(tmp_path, monkeypatch, codex, chunk) -> None:
    monkeypatch.setattr(message_cache, "READ_CHUNK", chunk, raising=False)
    parser = codex_lines if codex else claude_lines
    data = awkward(codex)
    final = legacy(data, parser)
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b"")
    cache = MessageCache(parser)
    for end in range(1, len(data) + 1):
        with path.open("ab") as output:
            output.write(data[end - 1 : end])
        seen = cache.read(path)
        assert seen == legacy(data[:end], parser)
        # Every record built from complete lines already has its final identity.
        complete = data[: data.rfind(b"\n", 0, end) + 1]
        settled = legacy(complete, parser)
        assert seen[: len(settled)] == final[: len(settled)]
    assert cache.read(path) == final


def test_unterminated_tail_is_shown_then_committed_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(message_cache, "READ_CHUNK", 5, raising=False)
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(line("one") + line("two")[:-1])
    cache = MessageCache(claude_lines)
    assert [row["id"] for row in cache.read(path)] == [0, 1]
    with path.open("ab") as output:
        output.write(b"\n" + line("three"))
    with patch.object(cache, "_parser", wraps=claude_lines) as parse:
        rows = cache.read(path)
    assert [(row["id"], row["blocks"][0]["text"]) for row in rows] == [
        (0, "one"),
        (1, "two"),
        (2, "three"),
    ]
    assert "one" not in str(parse.call_args_list)


class RecordingReads:
    """Patch target for Path.open that records every read size requested."""

    def __init__(self) -> None:
        self.sizes: list[int | None] = []
        real_open = Path.open
        sizes = self.sizes

        def open(path: Path, *args, **kwargs):
            stream = real_open(path, *args, **kwargs)
            real_read = stream.read

            def read(size: int | None = None) -> bytes:
                sizes.append(size)
                return real_read() if size is None else real_read(size)

            stream.read = read
            return stream

        self.open = open


@pytest.mark.parametrize("chunk", [4096, 64 * 1024])
def test_reads_are_chunked_including_a_record_longer_than_several_chunks(
    tmp_path, monkeypatch, chunk
) -> None:
    monkeypatch.setattr(message_cache, "READ_CHUNK", chunk, raising=False)
    path = tmp_path / "transcript.jsonl"
    long_text = "x" * (chunk * 5 + 123)
    data = line("before") + line(long_text) + line("after")
    path.write_bytes(data)
    reads = RecordingReads()
    cache = MessageCache(claude_lines)
    with patch.object(Path, "open", reads.open):
        rows = cache.read(path)
    assert rows == legacy(data, claude_lines)
    assert rows[1]["blocks"][0]["text"] == long_text
    assert reads.sizes and all(isinstance(n, int) and 0 < n <= chunk for n in reads.sizes)
    assert len(reads.sizes) >= len(data) // chunk

    appended = line("tail")
    with path.open("ab") as output:
        output.write(appended)
    reads.sizes.clear()
    with patch.object(Path, "open", reads.open):
        rows = cache.read(path)
    assert rows[-1] == {"id": 3, "role": "user", "blocks": [{"type": "text", "text": "tail"}]}
    assert all(isinstance(n, int) and 0 < n <= chunk for n in reads.sizes)
    # The committed prefix is hashed, never reparsed, and nothing is read twice.
    assert sum(reads.sizes) <= len(data) + len(appended)


def test_unchanged_files_are_not_reparsed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(message_cache, "READ_CHUNK", 7, raising=False)
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(awkward(False))
    cache = MessageCache(claude_lines)
    first = cache.response(path, "claude-code", "sid")
    # Recent file: the bytes are rehashed, but identical content keeps the payload.
    with patch.object(cache, "_parser", side_effect=AssertionError("reparsed")):
        assert cache.response(path, "claude-code", "sid") is first
    stat = path.stat()
    now = max(stat.st_mtime_ns, stat.st_ctime_ns) + 3_000_000_000
    monkeypatch.setattr(time, "time_ns", lambda: now)
    cache.read(path)
    # Settled file: stat alone decides, so it is not even opened.
    with patch.object(Path, "open", side_effect=AssertionError("reopened")):
        assert cache.response(path, "claude-code", "sid") is first
