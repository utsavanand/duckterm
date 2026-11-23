"""Annotations are stored and sent back to the agent as a follow-up prompt."""

import asyncio
import json
import tempfile
from pathlib import Path

from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server


class _W:
    def __init__(self) -> None:
        self.data = b""

    def write(self, b: bytes) -> None:
        self.data += b

    async def drain(self) -> None:
        pass


def test_annotation_stored_and_sent_to_agent() -> None:
    async def scenario() -> tuple[int, bytes]:
        store = HistoryStore(Path(tempfile.mkdtemp()) / "db.sqlite")
        server = Server(history=store)
        await server.orchestrator.launch(runtime=GenericRuntime("cat"), cwd="/tmp", session_key="S")
        sup = server.orchestrator.get("S")
        assert sup is not None
        await asyncio.sleep(0.3)
        got = bytearray()
        gen = sup.subscribe_bytes()

        async def collect() -> None:
            async for chunk in gen:
                got.extend(chunk)

        task = asyncio.create_task(collect())
        w = _W()
        body = json.dumps({"quote": "the leak", "note": "fix it"}).encode()
        await server._add_annotation(w, "S", body)  # type: ignore[arg-type]
        await asyncio.sleep(0.4)
        task.cancel()
        await server.orchestrator.stop("S")
        return len(store.annotations("S")), bytes(got)

    count, echoed = asyncio.run(scenario())
    assert count == 1  # stored
    # cat echoes the composed follow-up, proving it reached the agent's stdin.
    assert b'Re: "the leak" \xe2\x80\x94 fix it' in echoed or b"fix it" in echoed


def test_message_annotation_reference_round_trips_without_schema_change(tmp_path):
    path = tmp_path / "db.sqlite"
    store = HistoryStore(path)
    key = "a" * 64
    store.add_annotation("old", "S", "plain quote", "legacy", 1)
    store.add_annotation("anchored", "S", "same words", "original feedback", 2, message_key=key)
    assert store.annotations("S") == [
        {"id": "old", "quote": "plain quote", "note": "legacy", "created_at": 1},
        {
            "id": "anchored",
            "quote": "same words",
            "note": "original feedback",
            "created_at": 2,
            "message_key": key,
        },
    ]
    store.close()
    reopened = HistoryStore(path)
    assert reopened.annotations("S")[1]["message_key"] == key
    reopened.close()


def test_annotation_rejects_missing_or_changed_source(tmp_path, monkeypatch):
    async def scenario():
        store = HistoryStore(tmp_path / "db.sqlite")
        server = Server(history=store)
        source = ("codex", "/tmp", "native-id")
        monkeypatch.setattr(server, "_message_source", lambda key: source)
        monkeypatch.setattr(server, "_read_messages", lambda source: [])
        w = _W()
        await server._add_annotation(
            w,
            "S",
            json.dumps(
                {"quote": "same words", "note": "feedback", "message_key": "a" * 64}
            ).encode(),
        )
        assert b"409" in w.data
        assert store.annotations("S") == []
        monkeypatch.setattr(server, "_read_messages", lambda source: [{"message_key": "a" * 64}])
        w = _W()
        await server._add_annotation(
            w,
            "S",
            json.dumps(
                {"quote": "same words", "note": "feedback", "message_key": "a" * 64}
            ).encode(),
        )
        assert b"200" in w.data
        assert store.annotations("S")[0]["message_key"] == "a" * 64

        def rebound(_source):
            monkeypatch.setattr(
                server, "_message_source", lambda key: ("codex", "/tmp", "replacement")
            )
            return [{"message_key": "a" * 64}]

        monkeypatch.setattr(server, "_read_messages", rebound)
        w = _W()
        await server._add_annotation(
            w, "S", json.dumps({"note": "feedback", "message_key": "a" * 64}).encode()
        )
        assert b"409" in w.data
        assert len(store.annotations("S")) == 1
        w = _W()
        await server._add_annotation(w, "S", b'{"note":"feedback","message_key":"invalid"}')
        assert b"400" in w.data
        assert len(store.annotations("S")) == 1
        store.close()

    asyncio.run(scenario())
