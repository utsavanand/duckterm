"""HistoryStore's connection must only ever be used from the serving thread.

It is opened with check_same_thread=False and no lock, so safety rests on a
convention: every caller touches it from the event loop. #149 moved
connector_last_used into asyncio.to_thread, which raced the write paths and
corrupted cursor state (observed as an IndexError on a zero-column row).
These tests make the convention checkable instead of tribal.
"""

import asyncio
import json
import threading
from pathlib import Path

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


def test_concurrent_reads_and_writes_on_one_connection_corrupt_it(
    tmp_path: Path,
) -> None:
    """Why the convention exists. Without it the connection misbehaves, so a
    future to_thread caller is not merely untidy — it breaks writes too."""
    store = HistoryStore(tmp_path / "race.sqlite")
    try:
        for index in range(2000):
            store._conn.execute(
                "INSERT INTO events (session_key, event_type, ts, payload_json) "
                "VALUES (?, ?, ?, json(?))",
                (
                    f"s{index % 5}",
                    "PreToolUse",
                    1000 + index,
                    json.dumps({"tool_name": f"mcp__github__tool_{index}"}),
                ),
            )
        store._conn.commit()

        failures: list[str] = []
        stop = threading.Event()

        def read() -> None:
            while not stop.is_set():
                try:
                    store.connector_last_used()
                except Exception as exc:  # noqa: BLE001 - recording the class is the point
                    failures.append(f"read {type(exc).__name__}")

        def write() -> None:
            index = 0
            while not stop.is_set():
                try:
                    store._conn.execute(
                        "INSERT INTO events (session_key, event_type, ts, payload_json) "
                        "VALUES (?, ?, ?, json(?))",
                        (
                            f"w{index}",
                            "PreToolUse",
                            90000 + index,
                            json.dumps({"tool_name": "mcp__railway__deploy"}),
                        ),
                    )
                    store._conn.commit()
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"write {type(exc).__name__}")
                index += 1

        threads = [threading.Thread(target=read, daemon=True) for _ in range(2)]
        threads += [threading.Thread(target=write, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        stop.wait(3)
        stop.set()
        for thread in threads:
            thread.join(timeout=5)

        assert failures, (
            "expected sharing one unlocked connection across threads to fail; if "
            "this passes, HistoryStore gained a lock and the single-thread "
            "convention can be retired"
        )
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
