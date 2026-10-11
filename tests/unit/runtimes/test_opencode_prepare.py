import asyncio
import json
import sqlite3
import threading
import time

import pytest

from duckterm.persistence import opencode_launch as journal
from duckterm.runtimes.opencode_identity import Selection
from duckterm.runtimes.opencode_prepare import prepare
from duckterm.runtimes.opencode_protocol import OpenCodeError, UncertainWrite


@pytest.fixture
def fixture(tmp_path):
    path = tmp_path / "launch.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sessions (session_key TEXT PRIMARY KEY, runtime TEXT, restart_json TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES ('fixture', 'opencode', '{}')")
    conn.commit()
    state = journal.begin(
        conn,
        "fixture",
        journal.Launch(
            "gen",
            "local",
            "/fixture/store",
            str(tmp_path.resolve()),
            123,
            prompt="one prompt",
            model="fixture/model",
        ),
    )
    yield conn, state, path
    conn.close()


class Native:
    def __init__(self, fixture):
        self.conn, self.state, self.path = fixture
        self.directory = self.state.directory
        self.owner_thread = threading.get_ident()
        self.writes = []
        self.message = None
        self.lose = set()
        self.no_message = False
        self.reads = 0

    def health(self):
        assert threading.get_ident() != self.owner_thread

    def request(self, method, path, body=None):
        assert threading.get_ident() != self.owner_thread
        conn = sqlite3.connect(self.path)
        try:
            state = journal.read(conn, "fixture", "gen")
        finally:
            conn.close()
        if method == "POST":
            self.writes.append(path)
        if path == "/session":
            assert state.phase == "creating"
            result = {"id": "ses_fixture", "directory": self.directory}
        elif path == "/tui/select-session":
            assert state.phase == "selecting" and state.session_id == body["sessionID"]
            result = True
        elif path.endswith("/prompt_async"):
            assert state.phase == "submitting" and state.message_id == body["messageID"]
            self.message = {
                "info": {
                    "id": body["messageID"],
                    "sessionID": state.session_id,
                    "role": "user",
                    "model": body["model"],
                },
                "parts": [
                    {
                        "id": "prt_fixture",
                        "sessionID": state.session_id,
                        "messageID": state.message_id,
                        **body["parts"][0],
                    }
                ],
            }
            result = None  # Pinned native prompt_async acknowledgment is HTTP 204.
        else:
            assert path.endswith("/message/" + state.message_id)
            self.reads += 1
            if self.no_message or self.message is None:
                raise OpenCodeError("Native message not present")
            result = self.message
        if path in self.lose:
            raise UncertainWrite("Lost response after native acceptance")
        return result


async def selected(state):
    return Selection(
        state.generation,
        state.directory,
        state.pid,
        state.session_id,
        sequence=1,
        selected=True,
        observed_at=time.monotonic(),
    )


def run(fixture, native, **kwargs):
    conn, state, _ = fixture
    return asyncio.run(
        prepare(conn, "fixture", state.generation, native, selected, lambda: True, **kwargs)
    )


@pytest.mark.parametrize("lost", [None, "select", "prompt"])
def test_claims_commit_before_native_writes_and_lost_receipts_do_not_duplicate(fixture, lost):
    native = Native(fixture)
    if lost == "select":
        native.lose.add("/tui/select-session")
    if lost == "prompt":
        native.lose.add("/session/ses_fixture/prompt_async")
    state = run(fixture, native)
    assert state.phase == "ready"
    assert native.writes == ["/session", "/tui/select-session", "/session/ses_fixture/prompt_async"]
    assert native.reads == 1
    assert run(fixture, native) == state
    assert len(native.writes) == 3


def test_lost_creation_never_creates_second_conversation(fixture):
    native = Native(fixture)
    native.lose.add("/session")
    with pytest.raises(UncertainWrite):
        run(fixture, native)
    with pytest.raises(OpenCodeError, match="creation is uncertain"):
        run(fixture, native)
    assert native.writes == ["/session"]


def test_unobserved_prompt_survives_timeout_and_reconciles_on_restart(fixture):
    native = Native(fixture)
    native.no_message = True
    with pytest.raises(TimeoutError):
        run(fixture, native, timeout=0.1)
    conn, state, path = fixture
    assert journal.read(conn, "fixture", state.generation).phase == "submitting"
    native.no_message = False
    restarted = sqlite3.connect(path)
    try:
        recovered = run((restarted, state, path), native)
        assert recovered.phase == "ready"
        assert native.writes.count("/session/ses_fixture/prompt_async") == 1
    finally:
        restarted.close()


def test_selection_ack_without_native_proof_cannot_send_initial_prompt(fixture):
    native = Native(fixture)
    conn, state, _ = fixture

    async def unproven(s):
        return Selection(s.generation, s.directory, s.pid, s.session_id)

    with pytest.raises(OpenCodeError, match="has not confirmed"):
        asyncio.run(prepare(conn, "fixture", state.generation, native, unproven, lambda: True))
    assert native.writes == ["/session", "/tui/select-session"]
    assert journal.read(conn, "fixture", state.generation).phase == "selecting"


def test_changed_generation_during_native_selection_cannot_submit(fixture):
    native = Native(fixture)
    conn, state, _ = fixture

    async def replaced(s):
        data = json.loads(conn.execute("SELECT restart_json FROM sessions").fetchone()[0])
        data["opencode_launch"]["generation"] = "replacement"
        conn.execute("UPDATE sessions SET restart_json=?", (json.dumps(data),))
        conn.commit()
        return await selected(s)

    with pytest.raises(OpenCodeError, match="older launch"):
        asyncio.run(prepare(conn, "fixture", state.generation, native, replaced, lambda: True))
    assert native.writes == ["/session", "/tui/select-session"]


def test_dead_process_cannot_receive_prompt(fixture):
    native = Native(fixture)
    conn, state, _ = fixture
    alive = True

    async def died(s):
        nonlocal alive
        alive = False
        return await selected(s)

    with pytest.raises(OpenCodeError, match="no longer owns"):
        asyncio.run(prepare(conn, "fixture", state.generation, native, died, lambda: alive))
    assert native.writes == ["/session", "/tui/select-session"]


# Independent QA regression: recovery starts while the canceled native worker finishes.
def test_cancelled_inflight_prompt_recovers_without_second_write(fixture):
    conn, state, _ = fixture
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    native = Native(fixture)
    original = native.request

    def request(method, path, body=None):
        if path.endswith("/prompt_async"):
            entered.set()
            assert release.wait(5)
            try:
                return original(method, path, body)
            finally:
                finished.set()
        return original(method, path, body)

    native.request = request

    async def run():
        task = asyncio.create_task(
            prepare(conn, "fixture", state.generation, native, selected, lambda: True)
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert journal.read(conn, "fixture", state.generation).phase == "submitting"
            recovered = asyncio.create_task(
                prepare(conn, "fixture", state.generation, native, selected, lambda: True)
            )
            release.set()
            result = await recovered
            assert result.phase == "ready"
            assert finished.is_set()
            assert native.writes.count("/session") == 1
            assert native.writes.count("/session/ses_fixture/prompt_async") == 1
        finally:
            release.set()
            if not task.done():
                task.cancel()

    asyncio.run(run())
