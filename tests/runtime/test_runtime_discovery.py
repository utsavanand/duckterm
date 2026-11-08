"""Runtime discovery is owner-only and read-only in every edition."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from test_session_api import Writer

from duckterm.editions import CODE, FULL, LITE
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes import discovery
from duckterm.server import Server


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_discovery_keeps_owner_auth_and_does_not_change_session_data(
    tmp_path, monkeypatch, edition
):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    history = HistoryStore(tmp_path / "state.sqlite", edition=edition)
    server = Server(history=history, edition=edition)
    response = {"version": 1, "runtimes": []}
    discover = AsyncMock(return_value=response)
    monkeypatch.setattr(discovery, "discover", discover)

    async def request(headers):
        writer = Writer()
        await server._dispatch(
            "GET", "/runtimes/discovery", asyncio.StreamReader(), writer, headers, b""
        )
        head, body = writer.data.split(b"\r\n\r\n", 1)
        return int(head.split()[1]), body

    try:
        owner = {"x-duckterm-token": server.token}
        assert asyncio.run(request({}))[0] == 401
        assert asyncio.run(request({**owner, "origin": "https://evil.invalid"}))[0] == 403
        discover.assert_not_awaited()
        before = history._conn.total_changes
        code, body = asyncio.run(request(owner))
        assert code == 200 and json.loads(body) == response
        discover.assert_awaited_once_with()
        assert history._conn.total_changes == before
        assert history.sessions() == []
    finally:
        history.close()
