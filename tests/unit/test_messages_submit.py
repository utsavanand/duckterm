"""Messages sends one multiline paste, then a separate Enter, without retries."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from duckterm.server import Server


@pytest.mark.parametrize("fail_at", [0, 1, 2])
def test_followup_separates_paste_and_enter_and_reports_failure(monkeypatch, fail_at):
    async def scenario():
        writes = []
        responses = []

        async def write(data):
            writes.append((data, time.monotonic()))
            return len(writes) != fail_at

        async def respond(writer, status, body):
            responses.append((status, body))

        supervisor = SimpleNamespace(write_queued_bytes=write, write_input=AsyncMock())
        server = SimpleNamespace(orchestrator=SimpleNamespace(get=lambda _: supervisor))
        monkeypatch.setattr("duckterm.server._write_json", respond)
        monkeypatch.setattr(Server, "_SUBMIT_GAP_S", 0.02)
        await Server._input(
            server,
            None,
            "test-followup",
            json.dumps({"text": "first line\nsecond line", "submit": True}).encode(),
        )
        assert writes[0][0] == b"\x1b[200~first line\nsecond line\x1b[201~"
        if fail_at == 1:
            assert len(writes) == 1  # no Enter if the paste failed
        else:
            assert len(writes) == 2
            assert writes[1][0] == b"\r"
            assert writes[1][1] - writes[0][1] >= 0.02
        assert responses[0][0] == (409 if fail_at else 200)
        assert responses[0][1]["written"] == (not fail_at)
        supervisor.write_input.assert_not_called()

    asyncio.run(scenario())
