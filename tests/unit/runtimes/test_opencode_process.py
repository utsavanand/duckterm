import asyncio
import json
from pathlib import Path

import pytest

from duckterm.core.eventbus import EventBus
from duckterm.persistence import opencode_launch as journal
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.opencode_identity import Selection
from duckterm.runtimes.opencode_process import OpenCodeProcess
from duckterm.runtimes.opencode_protocol import VERSION, OpenCodeError


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    monkeypatch.delenv("OPENCODE_TUI_CONFIG", raising=False)
    history = HistoryStore(tmp_path / "duckterm/db.sqlite")
    EventBus(sink=history.record).publish(
        {
            "event_type": "SessionStart",
            "session_key": "fixture",
            "runtime": "opencode",
            "cwd": str(tmp_path),
            "test": True,
        }
    )
    process = OpenCodeProcess(
        "/bin/false",
        "",
        tmp_path,
        "fixture",
        "",
        {"HOME": str(tmp_path), "OPENCODE_DB": str(tmp_path / "native.db")},
        history,
    )

    async def verified():
        pass

    monkeypatch.setattr(process, "_verify_binary", verified)
    yield process
    history.delete_session("fixture")
    history.close()


def event(process, **changes):
    return {
        "version": VERSION,
        "pid": 12,
        "generation": process.generation,
        "directory": process.directory,
        "ready": True,
        "route": "home",
        "sessionID": None,
        "sequence": 1,
        **changes,
    }


async def post(process, value, *, token=None, size=None):
    port = process._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = json.dumps(value).encode()
    credential = process.token if token is None else token
    writer.write(
        (
            f"POST /observation HTTP/1.1\r\nAuthorization: Bearer {credential}\r\n"
            f"Content-Length: {len(body) if size is None else size}\r\n\r\n"
        ).encode()
        + body
    )
    await writer.drain()
    result = await asyncio.wait_for(reader.read(), 3)
    writer.close()
    await writer.wait_closed()
    return result.split(b"\r\n", 1)[0]


def test_observer_rejects_foreign_credentials_identity_sequence_and_oversized_payload(fixture):
    process = fixture

    async def run():
        await process.configure()
        process._pid = 12
        try:
            assert b"401" in await post(process, event(process), token="wrong")
            assert process._latest is None
            for change in [
                {"pid": 13},
                {"generation": "old"},
                {"version": "other"},
                {"directory": "/wrong"},
                {"sequence": True},
            ]:
                assert b"400" in await post(process, event(process, **change))
                assert process._latest is None
            assert b"400" in await post(process, event(process), size=8193)
            assert b"200" in await post(process, event(process))
            assert b"400" in await post(process, event(process))
            assert process._latest["sequence"] == 1
        finally:
            await process.close()

    asyncio.run(run())


def bind(process):
    conn = process.history._conn
    state = journal.Launch(process.generation, "local", "fixture-store", process.directory, 12)
    journal.begin(conn, "fixture", state)
    journal.claim_create(conn, "fixture", state.generation)
    return journal.bind_created(
        conn, "fixture", state.generation, {"id": "ses_fixture", "directory": process.directory}
    )


def test_conversation_switch_is_durably_contested_across_observer_restart(fixture):
    process = fixture
    state = bind(process)
    process._pid = 12
    process._selection = Selection(state.generation, state.directory, state.pid, state.session_id)
    process._accept(event(process, route="session", sessionID="ses_fixture"))
    process._selection.require_selected()
    process._accept(event(process, sequence=2, route="session", sessionID="ses_other"))
    assert process.history.restart_control("fixture")["native_binding"]["contested"]
    with pytest.raises(OpenCodeError, match="binding has changed"):
        journal.read(process.history._conn, "fixture", process.generation)
    with pytest.raises(OpenCodeError):
        process._selection.require_selected()


def test_recovery_never_replaces_an_occupied_observer_endpoint(fixture):
    process = fixture

    async def run():
        await process.configure()
        bind(process)
        process._save_connection(
            process._server.sockets[0].getsockname()[1], process.client.port, 12
        )
        try:
            with pytest.raises(OSError):
                await OpenCodeProcess.recover(
                    Path(process.directory), "fixture", {}, process.history
                )
            assert b"200" in await post(process, event(process))
        finally:
            await process.close()

    asyncio.run(run())


def test_recovery_refuses_replaced_store_before_any_native_write(fixture):
    process = fixture

    async def run():
        await process.configure()
        bind(process)
        process._recovering = True
        process._pid = 12
        process.database.parent.mkdir(parents=True, exist_ok=True)
        process.database.touch()
        process._accept(event(process))
        try:
            with pytest.raises(OpenCodeError, match="store was replaced"):
                await process.ready(12, lambda: True)
        finally:
            await process.close()

    asyncio.run(run())
