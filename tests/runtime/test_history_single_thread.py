"""HistoryStore enforces owner-thread access; worker I/O uses copied inputs."""

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


class Writer:
    def __init__(self) -> None:
        self.data = b""

    def write(self, chunk: bytes) -> None:
        self.data += chunk

    async def drain(self) -> None:
        pass


@pytest.fixture()
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    (tmp_path / "home").mkdir()
    history = HistoryStore(tmp_path / "db.sqlite")
    instance = Server(history=history)
    yield instance
    history.close()


def test_listing_connectors_never_touches_the_database_off_thread(
    server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression in one assertion: record which thread each query ran on."""
    serving: list[int] = []
    original = server.history.connector_last_used

    def watched() -> dict[str, tuple[int, int]]:
        serving.append(threading.get_ident())
        return original()

    monkeypatch.setattr(server.history, "connector_last_used", watched)

    async def exercise() -> int:
        writer = Writer()
        await server._dispatch(
            "GET",
            "/connectors",
            asyncio.StreamReader(),
            writer,
            {"host": "127.0.0.1", "x-duckterm-token": server.token},
            b"",
        )
        return threading.get_ident()

    loop_thread = asyncio.run(exercise())
    assert serving == [loop_thread], (
        "connector_last_used ran on a worker thread; HistoryStore's connection "
        "has no lock, so every caller must stay on the serving thread"
    )


def test_wrong_thread_reads_and_writes_fail_without_damaging_store(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "guard.sqlite")

    async def exercise() -> None:
        with pytest.raises(sqlite3.ProgrammingError, match="same thread"):
            await asyncio.to_thread(store.connector_last_used)
        with pytest.raises(sqlite3.ProgrammingError, match="same thread"):
            await asyncio.to_thread(store._conn.execute, "DELETE FROM events")
        assert store.connector_last_used() == {}
        assert store._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    try:
        asyncio.run(exercise())
    finally:
        store.close()


def test_messages_and_progress_use_worker_io_with_owner_thread_database(
    server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = threading.get_ident()
    seen = []
    server.history.record(
        {
            "_id": "start",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "probe",
            "session_id": "native",
            "runtime": "generic",
            "cwd": "/tmp",
            "test": True,
        }
    )

    def messages_response(**kwargs):
        assert threading.get_ident() != owner
        assert kwargs["session_id"] == "native"
        seen.append("messages")
        return b'{"messages": []}'

    def read_transcript(**kwargs):
        assert threading.get_ident() != owner
        assert kwargs["session_id"] == "native"
        seen.append("progress")
        return [{"role": "assistant", "text": "done"}]

    monkeypatch.setattr(
        "duckterm.server._build_runtime",
        lambda *args: SimpleNamespace(
            messages_response=messages_response, read_transcript=read_transcript
        ),
    )
    monkeypatch.setattr(
        "duckterm.server.summarize", lambda *args, **kwargs: SimpleNamespace(text="invalid")
    )

    async def exercise():
        writer = Writer()
        await server._messages(writer, "probe")
        assert b'"messages": []' in writer.data
        await server._refresh_progress("probe")

    asyncio.run(exercise())
    assert seen == ["messages", "progress"]
    server.history.purge_test_sessions()


def test_connector_index_survives_reopen_and_preserves_counts(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    store = HistoryStore(path)
    now = int(time.time() * 1000)
    for index, tool in enumerate(
        [
            "Bash",
            "mcp__github__one",
            "mcp__github__two",
            "mcp__railway__deploy",
            "mcp____empty",
            "mcpXXgithub__wrong",
        ]
    ):
        store._conn.execute(
            "INSERT INTO events(event_type,ts,payload_json) VALUES(?,?,?)",
            ("PreToolUse", now + index, json.dumps({"tool_name": tool})),
        )
    store._conn.commit()
    store.close()
    store = HistoryStore(path)
    try:
        assert store.connector_last_used() == {"github": (now + 2, 2), "railway": (now + 3, 1)}
        plan = store._conn.execute(
            "EXPLAIN QUERY PLAN SELECT json_extract(payload_json, '$.tool_name') AS tool, ts "
            "FROM events WHERE event_type = 'PreToolUse' AND tool LIKE 'mcp!_!_%' ESCAPE '!'"
        ).fetchall()
        assert any("idx_connector_usage" in str(row[3]) for row in plan)
    finally:
        store.close()


def test_a_connector_with_no_usage_is_absent_rather_than_an_empty_row(
    tmp_path: Path,
) -> None:
    """The hypothesis this bug was first attributed to. Zero matching rows
    returns an empty mapping, so the caller's .get() yields None; nothing is
    indexed and there is no empty row to read."""
    store = HistoryStore(tmp_path / "empty.sqlite")
    try:
        store._conn.execute(
            "INSERT INTO events (session_key, event_type, ts, payload_json) "
            "VALUES (?, ?, ?, json(?))",
            ("a", "PreToolUse", 1, json.dumps({"tool_name": "Bash"})),
        )
        store._conn.commit()
        assert store.connector_last_used() == {}
    finally:
        store.close()
