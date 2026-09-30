"""Oracle voice endpoints fail honestly, so the dashboard can fall back to a
macOS voice instead of going silent."""

import json

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server
from duckterm.voice import service


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    yield Server(history=history)
    history.close()


def owner(server):
    return {"x-duckterm-token": server.token}


def test_unsupported_machine_is_told_so_and_cannot_install(server, monkeypatch) -> None:
    monkeypatch.setattr(service.sys, "platform", "linux")
    status, body = dispatch(server, "GET", "/voice/status", {})
    assert (status, body["state"]) == (200, "unsupported")
    status, body = dispatch(server, "POST", "/voice/install", owner(server))
    assert (status, body["reason"]) == (409, "Natural voices need a Mac.")


def test_speaking_before_install_is_a_503_with_the_reason(server, monkeypatch) -> None:
    monkeypatch.setattr(service, "unsupported_reason", lambda: None)
    assert dispatch(server, "GET", "/voice/status", {})[1] == {
        "state": "absent",
        "size": service.SIZE_NOTE,
    }
    request = json.dumps({"text": "architect needs your input", "voice": "af_heart"}).encode()
    status, body = dispatch(server, "POST", "/voice/say", owner(server), request)
    assert (status, body["error"]) == (503, "Natural voices aren't installed.")


def test_voice_routes_need_the_owner_token(server) -> None:
    assert dispatch(server, "POST", "/voice/say", {}, b"{}")[0] == 401
    assert dispatch(server, "POST", "/voice/install", {})[0] == 401
    assert dispatch(server, "DELETE", "/voice", {})[0] == 401
