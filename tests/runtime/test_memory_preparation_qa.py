"""Independent preparation concurrency and cancellation boundaries."""

import asyncio

from tests.runtime.test_memory import memory_rig
from tests.runtime.test_memory_preparation import preparation, request

from duckterm.core.session_api import APIError

__all__ = ["memory_rig", "preparation"]


def test_concurrent_same_retry_key_cannot_bind_two_different_targets(preparation, monkeypatch):
    server, _, _ = preparation

    async def run():
        entered = 0
        both = asyncio.Event()
        original = server.restarts.live_plan

        async def barrier(*args):
            nonlocal entered
            entered += 1
            if entered == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 2)
            return await original(*args)

        monkeypatch.setattr(server.restarts, "live_plan", barrier)
        left = request(server, "same-key")
        right = request(server, "same-key")
        right["binding"]["target"]["model"]["id"] = "different-model"
        try:
            results = await asyncio.gather(
                server.memory_preparation.start("agent", left),
                server.memory_preparation.start("agent", right),
                return_exceptions=True,
            )
            successes = [r for r in results if isinstance(r, dict)]
            conflicts = [r for r in results if isinstance(r, APIError) and r.status == 409]
            assert len(successes) == 1 and len(conflicts) == 1
            assert len(server.memory_preparation.jobs) == 1
        finally:
            await server.memory_preparation.close()

    asyncio.run(run())


def test_cancel_last_lease_during_capture_cannot_write_marker(preparation, monkeypatch):
    server, _, _ = preparation

    async def run():
        entered, released = asyncio.Event(), asyncio.Event()
        original = server.memory_preparation.validate_sources

        async def pause(*args):
            entered.set()
            await released.wait()
            return await original(*args)

        monkeypatch.setattr(server.memory_preparation, "validate_sources", pause)
        view = await server.memory_preparation.start("agent", request(server))
        task = server.memory_preparation.tasks[view["preparation_id"]]
        try:
            await asyncio.wait_for(entered.wait(), 2)
            server.memory_preparation.cancel("agent", view["preparation_id"], "dialog-1")
            released.set()
            await task
            assert (
                server.memory_preparation.status("agent", view["preparation_id"])["state"]
                == "canceled"
            )
            assert server.history.checkpoints("agent") == []
            assert (
                server.history._conn.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 0
            )
        finally:
            released.set()
            await server.memory_preparation.close()

    asyncio.run(run())
