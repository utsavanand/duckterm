"""HTTP status and detail responses must respect preparation invalidation."""

import asyncio
import json

import pytest
from tests.runtime.test_memory import memory_rig
from tests.runtime.test_memory_preparation import preparation, prepare
from tests.runtime.test_session_api import dispatch

__all__ = ["memory_rig", "preparation"]


@pytest.mark.parametrize("change", ["notes", "cancel"])
def test_invalidated_http_status_and_details_do_not_return_old_brief(preparation, change):
    server, _, _ = preparation
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready"
    identity = view["preparation_id"]
    path = f"/sessions/agent/restart-preparation/{identity}"
    headers = {"x-duckterm-token": server.token}
    status, ready = dispatch(server, "GET", path, headers)
    assert status == 200 and "brief" not in ready["proof"]
    status, detail = dispatch(server, "GET", path + "?detail=full", headers)
    assert status == 200 and "Retain glacier originals" in detail["brief"]["text"]
    if change == "notes":
        server.history.set_meta("agent", notes="Changed owner constraint")
        expected = "stale_source"
    else:
        assert dispatch(server, "DELETE", path + "?request_key=dialog-1", headers)[0] == 200
        expected = "canceled"
    status, invalid = dispatch(server, "GET", path, headers)
    assert status == 200 and invalid["state"] == expected
    assert "proof" not in invalid
    assert "Retain glacier originals" not in json.dumps(invalid)
    status, denied = dispatch(server, "GET", path + "?detail=full", headers)
    assert status == 409
    assert "brief" not in denied and "Retain glacier originals" not in json.dumps(denied)
