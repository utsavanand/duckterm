import asyncio
import json
import time

import pytest
from test_opencode_process import fixture

from duckterm.persistence import opencode_launch as journal
from duckterm.runtimes import opencode_process as module
from duckterm.runtimes.opencode_identity import Selection

__all__ = ["fixture"]


@pytest.mark.parametrize("lost_during", ["snapshot", "persist"])
def test_dead_owned_process_cannot_publish_idle_after_worker_returns(
    fixture, monkeypatch, lost_during
):
    process = fixture
    process.database.touch()
    info = process.database.stat()
    state = journal.Launch(
        process.generation,
        "local",
        json.dumps([str(process.database), info.st_dev, info.st_ino]),
        process.directory,
        12,
    )
    conn = process.history._conn
    journal.begin(conn, "fixture", state)
    journal.claim_create(conn, "fixture", state.generation)
    state = journal.bind_created(
        conn, "fixture", state.generation, {"id": "ses_fixture", "directory": state.directory}
    )
    process.client = object()
    process._selection = Selection(
        state.generation,
        state.directory,
        12,
        state.session_id,
        selected=True,
        observed_at=time.monotonic(),
    )
    alive = True
    reports = []

    async def worker(fn, *args):
        nonlocal alive
        if fn is module.snapshot:
            if lost_during == "snapshot":
                alive = False
            return [], "idle"
        assert fn is module.native_history.persist_observed
        if lost_during == "persist":
            alive = False
        return True, "fixture-content-hash"

    async def pause(_):
        pass

    def report(status):
        reports.append(status)
        process._closed = True

    monkeypatch.setattr(module.asyncio, "to_thread", worker)
    monkeypatch.setattr(module.asyncio, "sleep", pause)
    monkeypatch.setattr(process, "_report_status", report)
    asyncio.run(process._watch(lambda: alive))
    assert reports == ["unknown"]
    assert (
        process.history.restart_control("fixture").get("opencode_observation", {}).get("state")
        != "idle"
    )
