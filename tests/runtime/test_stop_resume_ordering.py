"""Resume cannot overtake Stop's early paused-state notification."""

import asyncio

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def test_resume_waits_until_stop_has_finished(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    history = HistoryStore(tmp_path / "history.sqlite")
    history.record(
        {
            "_id": "start",
            "_ts": 1,
            "session_key": "qa",
            "event_type": "SessionStart",
            "runtime": "generic",
            "cwd": str(tmp_path),
            "command": "cat",
            "launched": True,
            "test": True,
        }
    )
    server = Server(history=history)

    async def run():
        stopping, release_stop, attempted_resume = asyncio.Event(), asyncio.Event(), asyncio.Event()
        launches = []
        monkeypatch.setattr(server.orchestrator, "get", lambda key: object())

        async def stop(key):
            stopping.set()
            await release_stop.wait()
            server.bus.publish({"event_type": "SessionEnd", "session_key": key})
            return True

        async def launch(**kwargs):
            launches.append(kwargs)
            server.bus.publish(
                {
                    "event_type": "SessionStart",
                    "session_key": "qa",
                    "runtime": "generic",
                    "launched": True,
                    "test": True,
                }
            )
            return "qa"

        async def write_json(*args, **kwargs):
            pass

        monkeypatch.setattr(server.orchestrator, "stop", stop)
        monkeypatch.setattr(server.orchestrator, "launch", launch)
        monkeypatch.setattr("duckterm.server._write_json", write_json)
        stopped = asyncio.create_task(server._stop(None, "qa"))
        await stopping.wait()
        assert history.session("qa")["state"] == "stopped"

        async def resume():
            attempted_resume.set()
            return await server._resume_session("qa")

        resumed = asyncio.create_task(resume())
        await attempted_resume.wait()
        try:
            assert not launches, "Resume launched before Stop's final lifecycle write"
        finally:
            release_stop.set()
            await stopped
            status, result = await resumed
        assert status == 200 and result["resumed"]
        assert len(launches) == 1
        assert history.session("qa")["state"] == "busy"

    try:
        asyncio.run(run())
    finally:
        server.digests.close()
        history.purge_test_sessions()
        history.close()
