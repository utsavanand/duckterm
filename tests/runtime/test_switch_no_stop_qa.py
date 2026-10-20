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
        result = await server.restarts.request("a", "", target)
        assert result["status"] == "queued"
        assert result["draft_clear"] is True
        assert result["after_turn"] is True
        assert not server.restarts.turn_finished("a")
        # Simulate recovery of the durable queue; no real Stop hook arrived.
        await server.restarts.close()
        server.restarts = Restarts(server)
        try:
            await server.restarts.execute("a")
            assert server.restarts.read("a")["status"] == "queued"
            assert not calls
            assert server.history.session("a")["state"] == "busy"
        finally:
            await server.restarts.close()

    asyncio.run(run())
