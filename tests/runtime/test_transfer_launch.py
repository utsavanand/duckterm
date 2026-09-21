"""A lost launch reply or server restart cannot start a second destination process."""

import asyncio
import base64
import json
import shlex
import shutil
import sys
import uuid

import pytest

from duckterm import transfer_api, transfers
from duckterm.agents import tmux
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.mark.skipif(not shutil.which("tmux"), reason="requires tmux")
def test_launch_retry_after_server_restart_keeps_same_process(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("synthetic project")
    local, remote = tmp_path / "local", tmp_path / "remote"
    monkeypatch.setenv("DUCKTERM_HOME", str(local))
    identifier = uuid.uuid4().hex
    review = transfers.scan(source, [])
    transfers.prepare(identifier, str(source), [], review["fingerprint"])
    archive = (local / "transfers" / identifier / "snapshot.tar").read_bytes()
    monkeypatch.setenv("DUCKTERM_HOME", str(remote))
    transfers.begin(
        identifier, str(tmp_path / "destination"), len(archive), transfers.digest(archive)
    )
    transfers.receive(identifier, 0, base64.b64encode(archive).decode())
    transfers.finish(identifier)
    history = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=history)
    request = {
        "id": identifier,
        "command": shlex.join(
            [
                sys.executable,
                "-u",
                "-c",
                "import time; print('SYNTHETIC_READY', flush=True); time.sleep(60)",
            ]
        ),
        "test": True,
    }

    async def scenario():
        try:
            result = await transfer_api.dispatch(server, "launch", request)
            key = result["session_key"]
            row = history.session(key)
            assert row and row["test"] == 1
            before = tmux._tmux("list-panes", "-t", tmux.target_for(key), "-F", "#{pane_pid}")
            restarted = Server(history=history)
            again = await transfer_api.dispatch(restarted, "launch", request)
            after = tmux._tmux("list-panes", "-t", tmux.target_for(key), "-F", "#{pane_pid}")
            assert before == after
            assert again["session_key"] == key
            assert len(history.sessions()) == 1
        finally:
            await server.orchestrator.stop("transfer-" + identifier)
            history.purge_test_sessions()

    asyncio.run(scenario())


def test_source_resume_requires_explicit_continuation(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    identifier = uuid.uuid4().hex
    with transfers.locked(identifier) as directory:
        transfers.save(directory, {"stage": "moved", "id": identifier, "source_session": "source"})
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))

    class Writer:
        data = b""

        def write(self, data):
            self.data += data

        async def drain(self):
            pass

    writer = Writer()
    asyncio.run(server._resume(writer, "source"))
    assert b"409" in writer.data
    assert "Continue locally" in json.loads(writer.data.split(b"\r\n\r\n")[1])["error"]
    asyncio.run(transfer_api.dispatch(server, "continue", {"source_session": "source"}))
    assert transfers.session_transfer("source") is None
