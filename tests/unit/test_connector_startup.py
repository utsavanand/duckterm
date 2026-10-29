"""Desktop startup refreshes connector paths without changing hosted or Lite behavior."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from duckterm import connectors, editions, server
from duckterm.agents import hooks_install


class ListeningReached(Exception):
    """Stop before serving or launching background workers."""


@pytest.fixture
def startup(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("DUCKTERM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "fixture-state"))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    listener = SimpleNamespace(
        sockets=[SimpleNamespace(getsockname=lambda: ("127.0.0.1", 4400))],
        close=Mock(),
        wait_closed=AsyncMock(),
        start_serving=AsyncMock(side_effect=ListeningReached),
    )
    monkeypatch.setattr(server.asyncio, "start_server", AsyncMock(return_value=listener))
    monkeypatch.setattr(server.session_instructions, "refresh_guides", lambda **kwargs: 0)
    monkeypatch.setattr(hooks_install, "repair_moved_app_hooks", lambda **kwargs: [])
    monkeypatch.setattr(editions, "remember", lambda edition: None)

    def start(edition, events=None):
        async def reconcile():
            if events is not None:
                events.append("reconcile")
            return []

        app = SimpleNamespace(
            edition=edition,
            handle=Mock(),
            history=SimpleNamespace(
                sessions=lambda: [], session_api=SimpleNamespace(set_url=Mock())
            ),
            orchestrator=SimpleNamespace(reconcile=AsyncMock(side_effect=reconcile)),
        )
        with pytest.raises(ListeningReached):
            asyncio.run(server.Server.serve(app, "127.0.0.1", 4400, home_lock=tmp_path / "lock"))
        listener.start_serving.assert_awaited_once()
        listener.close.assert_called_once()
        listener.wait_closed.assert_awaited_once()
        return app

    return start


@pytest.mark.parametrize("edition", [editions.LITE, editions.CODE, editions.FULL])
def test_only_connector_editions_refresh_before_reconciliation(startup, monkeypatch, edition):
    events = []
    refresh = Mock(side_effect=lambda: events.append("refresh"))
    monkeypatch.setattr(connectors, "refresh_runtime_registration", refresh)
    original = server.asyncio.start_server

    async def bind(*args, **kwargs):
        events.append("bound")
        return await original(*args, **kwargs)

    monkeypatch.setattr(server.asyncio, "start_server", bind)
    app = startup(edition, events)
    app.orchestrator.reconcile.assert_awaited_once()
    assert events == (
        ["bound", "reconcile"] if edition is editions.LITE else ["bound", "refresh", "reconcile"]
    )
    assert refresh.call_count == (0 if edition is editions.LITE else 1)


@pytest.mark.parametrize(
    "error",
    [OSError("fixture moved"), ValueError("fixture custom"), RuntimeError("fixture unavailable")],
)
def test_refresh_failure_does_not_prevent_listening(startup, monkeypatch, capsys, error):
    monkeypatch.setattr(connectors, "refresh_runtime_registration", Mock(side_effect=error))
    startup(editions.CODE)
    assert "could not refresh connector launch paths" in capsys.readouterr().err


def test_hosted_startup_keeps_administrator_configuration(startup, monkeypatch, tmp_path):
    monkeypatch.setenv("DUCKTERM_HOSTED", "1")
    monkeypatch.setattr(connectors, "_duckterm_bin", lambda: pytest.fail("desktop CLI resolved"))
    startup(editions.FULL)
    assert list(tmp_path.iterdir()) == []
