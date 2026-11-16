import asyncio
from types import SimpleNamespace

import pytest
from test_maintained_progress import provider
from test_memory import memory_rig, transcript
from test_session_api import dispatch, enroll

from duckterm import memory_reconcile
from duckterm.core import memory_refresh

__all__ = ["memory_rig"]
REAL = memory_refresh.reconcile_reply


@pytest.mark.parametrize("failure", [TypeError, RuntimeError, OSError])
def test_optional_cleanup_failure_does_not_fail_checkpoint(memory_rig, monkeypatch, failure):
    from duckterm import server as sm

    server, headers, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep owner instructions intact.")
    provider(monkeypatch)
    writer = sm.summarize

    def generate(prompt, **kwargs):
        if "MEMORY UPDATE:\n" in prompt:
            return writer(prompt)
        return SimpleNamespace(text='{"ops":[]}', failure_reason=None)

    async def fail(*args, **kwargs):
        raise failure("injected optional cleanup failure")

    monkeypatch.setattr(memory_refresh, "reconcile_reply", REAL)
    monkeypatch.setattr(sm, "summarize", generate)
    monkeypatch.setattr(memory_reconcile, "clean_up", fail)
    result = asyncio.run(
        server._create_checkpoint("agent", server.history.session("agent"), "manual")
    )
    update = result["record"]["summary_update"]
    assert update["state"] in {"updated", "reused"}, update
    assert update["cleanup"]["state"] == "failed"
    assert dispatch(server, "GET", "/api/v1/session/memory/items", headers)[1]["items"]


def test_items_auth_peer_scope_and_parameter_injection(memory_rig, monkeypatch):
    server, headers, peer, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep owner instructions intact.")
    provider(monkeypatch)
    asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    route = "/api/v1/session/memory/items"
    own = dispatch(server, "GET", route, headers)
    assert own[0] == 200 and own[1]["items"]
    assert dispatch(server, "GET", route, {})[0] == 401
    assert dispatch(server, "GET", route, peer) == (200, {"items": []})
    assert dispatch(server, "GET", route + "?session=agent", peer)[0] == 400
    server.history.set_meta("agent", group="Elsewhere")
    moved = enroll(server.history, "agent", "Elsewhere")
    # Owner, 2026-11-11: memory belongs to the agent and moves with it.
    assert dispatch(server, "GET", route, moved) == own
    assert dispatch(server, "GET", route, peer) == (200, {"items": []})
