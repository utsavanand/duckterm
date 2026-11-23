"""An exhausted source is not implicit permission to interrupt its process."""

import asyncio

import pytest
from tests.runtime import test_restart_harness_switch as fixtures

from duckterm.restarts import Restarts

rig = fixtures.rig
offline = fixtures.offline


@pytest.mark.parametrize("target", [None, "claude-code"])
def test_default_restart_never_treats_quota_output_as_turn_completion(rig, target):
    server, _, screen, calls = rig
    screen[0] = "Usage limit reached. Try again later.\n────────────────\n› \n────────────────"

    async def run():
        # A switch needs a prepared proof; an ordinary restart does not.
        proof = await fixtures.prepare(server, target) if target else None
        key = "switch" if target else None
        await server.restarts.request("a", "", target, memory=proof, request_key=key)
        result = await server.restarts.describe("a")
        assert result["status"] == "queued"
        assert result["draft_clear"] is True
        assert result["after_turn"] is True
        assert not server.restarts.turn_finished("a")
        # Simulate recovery of the durable queue; no real Stop hook arrived.
        await server.restarts.close()
        server.restarts = Restarts(server)
        try:
            await server.restarts.execute("a")
            # A restart drops preparations, so a queued switch fails closed;
            # a plain restart stays queued for a real turn end.
            assert server.restarts.read("a")["status"] == ("failed" if target else "queued")
            assert not calls
            assert server.history.session("a")["state"] == "busy"
        finally:
            await server.restarts.close()

    asyncio.run(run())
