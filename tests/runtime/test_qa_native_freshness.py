import asyncio
import json
from types import SimpleNamespace

from duckterm.runtimes.native_history import NativeSnapshot
from duckterm.runtimes.opencode import OpenCodeRuntime
from duckterm.server import Server


def test_older_response_not_labelled_as_newer_observation(monkeypatch):
    import duckterm.server as module

    source = NativeSnapshot("/unused", "gen", "local", "store", "native")
    history = SimpleNamespace(
        session=lambda key: {"state": "idle"},
        restart_control=lambda key: {
            "opencode_observation": {"state": "idle", "observed_at": 100000, "revision": 2}
        },
    )
    server = SimpleNamespace(
        history=history,
        _message_source=lambda key: source,
        _read_messages_response=lambda source: json.dumps(
            {"messages": [], "history_status": "cached", "observed_at": 1000, "revision": 1}
        ).encode(),
    )
    replies = []

    async def write(writer, status, body):
        replies.append(json.loads(body))

    monkeypatch.setattr(module, "_write_json_bytes", write)
    monkeypatch.setattr(module.time, "time", lambda: 101)
    asyncio.run(Server._messages(server, None, "fixture"))
    assert replies[0]["transcript"]["status"] == "native_cached"
    assert replies[0]["transcript"]["checked_at"] == 1000


def test_user_command_round_trips_without_ephemeral_arguments():
    command = "'/tmp/a binary/opencode' --model provider/model"
    runtime = OpenCodeRuntime(command)
    saved = runtime.command_for_history(
        ["/tmp/a binary/opencode", "--port", "12345", "--dir", "/tmp/private"]
    )
    restored = OpenCodeRuntime(saved)
    assert restored._argv == runtime._argv
    assert restored.model == "provider/model"
    assert "--port" not in saved and "/tmp/private" not in saved


def test_identical_rechecked_content_can_be_current_without_rewriting_snapshot(monkeypatch):
    import duckterm.server as module

    source = NativeSnapshot("/unused", "gen", "local", "store", "native")
    observation = dict(state="idle", observed_at=100000, revision=2, content_hash="a" * 64)
    payload = dict(
        messages=[], history_status="cached", observed_at=1000, revision=1, content_hash="a" * 64
    )
    history = SimpleNamespace(
        session=lambda key: {"state": "idle"},
        restart_control=lambda key: {"opencode_observation": observation},
    )
    server = SimpleNamespace(
        history=history,
        _message_source=lambda key: source,
        _read_messages_response=lambda source: json.dumps(payload).encode(),
    )
    replies = []

    async def write(writer, status, body):
        replies.append(json.loads(body))

    monkeypatch.setattr(module, "_write_json_bytes", write)
    monkeypatch.setattr(module.time, "time", lambda: 101)
    asyncio.run(Server._messages(server, None, "fixture"))
    assert replies[-1]["transcript"] == {"status": "native_current", "checked_at": 100000}
    observation["content_hash"] = "b" * 64
    asyncio.run(Server._messages(server, None, "fixture"))
    assert replies[-1]["transcript"] == {"status": "native_cached", "checked_at": 1000}
