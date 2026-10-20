"""Malformed hook evidence must not permanently contest a recorded identity."""

import json

import pytest
from tests.runtime import test_launch_identity as fixtures
from tests.runtime.test_session_api import dispatch

from duckterm.server import Server

history = fixtures.history


@pytest.mark.parametrize("reported_id", ["invalid/path", {"id": "not-a-string"}])
def test_malformed_current_generation_hook_does_not_contest_assignment(history, reported_id):
    fixtures.seed(history)
    binding = {
        "runtime": "claude-code",
        "native_id": "recorded-id",
        "source": "assigned",
        "generation": "current",
    }
    history.set_restart_control("old", {"native_binding": binding})
    server = Server(history=history)
    try:
        status, body = dispatch(
            server,
            "POST",
            "/events",
            {"x-duckterm-token": server.token},
            json.dumps(
                {
                    "event_type": "SessionStart",
                    "runtime": "claude-code",
                    "session_key": "old",
                    "launch_generation": "current",
                    "session_id": reported_id,
                }
            ).encode(),
        )
        assert status == 200 and "dropped" in body
        assert history.native_identity("old") == {
            "native_id": "recorded-id",
            "source": "assigned",
            "status": "recorded",
        }
        assert history.restart_control("old")["native_binding"] == binding
    finally:
        server.digests.close()
