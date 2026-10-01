"""HTTP Messages polling reuses immutable JSON instead of copying object graphs."""

import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.runtimes.claude_code import _parse_message_lines as claude_lines
from duckterm.runtimes.codex import CodexRuntime
from duckterm.runtimes.codex import _parse_message_lines as codex_lines
from duckterm.runtimes.message_cache import MessageCache
from duckterm.server import Server


def record(text: str, codex: bool) -> bytes:
    obj = (
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}}
        if codex
        else {"type": "user", "message": {"role": "user", "content": text}}
    )
    return json.dumps(obj).encode() + b"\n"


@pytest.mark.parametrize("codex", [False, True])
def test_http_snapshot_matches_existing_keys_and_reuses_without_mutable_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: bool
) -> None:
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(record("hello", codex))
    runtime = CodexRuntime if codex else ClaudeCodeRuntime
    name = "codex" if codex else "claude-code"
    monkeypatch.setattr(runtime, "locate_transcript", lambda *args, **kwargs: path)
    store = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=store)
    store.record(
        {
            "_id": "start",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "test",
            "session_id": "native",
            "cwd": str(tmp_path),
            "runtime": name,
            "test": True,
        }
    )
    try:
        expected = json.dumps({"messages": server._session_messages("test")}).encode()
        first = server._session_messages_response("test")
        assert first == expected
        with (
            patch.object(runtime, "messages", side_effect=AssertionError("mutable response path")),
            patch.object(Path, "open", side_effect=AssertionError("unchanged transcript read")),
            patch(
                "duckterm.runtimes.message_cache.json.dumps",
                side_effect=AssertionError("warm JSON serialization"),
            ),
        ):
            assert server._session_messages_response("test") is first
        status, payload = dispatch(server, "GET", "/sessions/test/messages", {})
        assert status == 200
        assert payload == json.loads(expected)
        payload["messages"][0]["blocks"][0]["text"] = "poison"
        assert server._session_messages_response("test") == expected
        path.write_bytes(record("rewritten and longer", codex))
        changed = server._session_messages_response("test")
        assert json.loads(changed)["messages"][0]["blocks"][0]["text"] == "rewritten and longer"
        assert changed == json.dumps({"messages": server._session_messages("test")}).encode()
    finally:
        store.purge_test_sessions()
        store.close()


@pytest.mark.parametrize("codex", [False, True])
def test_response_scope_append_and_mutable_views_are_isolated(tmp_path: Path, codex: bool) -> None:
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(record("first", codex))
    parser = codex_lines if codex else claude_lines
    cache = MessageCache(parser)
    before = cache.response(path, "runtime", "native-a")
    returned = cache.read(path)
    blocks = returned[0]["blocks"]
    assert isinstance(blocks, list)
    blocks[0]["text"] = "poison"
    assert cache.response(path, "runtime", "native-a") == before
    other = cache.response(path, "runtime", "native-b")
    assert (
        json.loads(other)["messages"][0]["message_key"]
        != json.loads(before)["messages"][0]["message_key"]
    )
    with path.open("ab") as output:
        output.write(record("second", codex))
    messages = json.loads(cache.response(path, "runtime", "native-a"))["messages"]
    assert [m["blocks"][0]["text"] for m in messages] == ["first", "second"]
    expected_key = hashlib.sha256(
        json.dumps(
            ["runtime", "native-a", parser([record("first", codex).decode()], 0)[0]],
            sort_keys=True,
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    assert messages[0]["message_key"] == expected_key


def test_twenty_thousand_message_warm_response_does_not_scale_with_object_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "large.jsonl"
    path.write_bytes(record("x" * 200, False) * 20_000)
    cache = MessageCache(claude_lines)
    start = time.perf_counter()
    cold = cache.response(path, "claude-code", "native")
    cold_seconds = time.perf_counter() - start
    elapsed = []
    for _ in range(10):
        start = time.perf_counter()
        warm = cache.response(path, "claude-code", "native")
        elapsed.append(time.perf_counter() - start)
        assert warm is cold
    # Relative to the same-run cold path avoids hardware-specific millisecond limits.
    assert sorted(elapsed)[len(elapsed) // 2] < cold_seconds / 10
    assert len(json.loads(warm)["messages"]) == 20_000
