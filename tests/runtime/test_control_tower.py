"""Control tower endpoints: fleet insights, and owner messages to one session."""

import json
import time

import pytest
from tests.runtime.test_oracle import CODEX_DRAFT, CODEX_EMPTY
from tests.runtime.test_session_api import dispatch, enroll

from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server


class Terminal:
    def __init__(self, screen: str) -> None:
        self.running = True
        self.runtime = GenericRuntime("true")
        self.screen = screen
        self.last_owner_input_ms = 0
        self.pasted: list[bytes] = []

    def visible_screen(self) -> str:
        return self.screen

    def write_bytes(self, data: bytes) -> bool:
        self.pasted.append(data)
        return True


@pytest.fixture
def tower(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    for key, folder in [("shared", "work"), ("private", "")]:
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "test": True,
                "runtime": "codex",
            }
        )
        history.record({"_id": key + "-stop", "_ts": 2, "event_type": "Stop", "session_key": key})
        history.set_meta(key, name=key, group=folder)
    enroll(history, "shared")
    server = Server(history=history)
    terminal = Terminal(CODEX_EMPTY)
    monkeypatch.setattr(
        server.orchestrator, "get", lambda key: terminal if key == "shared" else None
    )
    yield server, {"x-duckterm-token": server.token}, terminal, history
    history.close()


def send(server, headers, key, text, mode):
    body = json.dumps({"text": text, "mode": mode}).encode()
    return dispatch(server, "POST", f"/sessions/{key}/message", headers, body)


def test_inbox_message_lands_as_an_owner_notice_for_that_session_only(tower) -> None:
    server, owner, terminal, history = tower
    assert send(server, {}, "shared", "hi", "inbox")[0] == 401
    status, body = send(server, owner, "shared", "please rerun QA", "inbox")
    assert (status, body["delivered"]) == (200, "inbox")
    inbox = history.session_api.inbox("shared", owner=True)["messages"]
    assert [(m["question"], m["sender_kind"]) for m in inbox] == [("please rerun QA", "owner")]
    assert terminal.pasted == []


def test_session_without_a_shared_folder_has_no_inbox(tower) -> None:
    server, owner, _, _ = tower
    status, body = send(server, owner, "private", "hi", "inbox")
    assert status == 409
    assert "isn't in a shared folder" in body["error"]


def test_typing_into_the_prompt_pastes_only_when_every_gate_passes(tower) -> None:
    server, owner, terminal, history = tower
    status, body = send(server, owner, "shared", "go ahead", "prompt")
    assert (status, body["delivered"]) == (200, "prompt")
    assert terminal.pasted == [b"\x1b[200~go ahead\x1b[201~\r"]

    terminal.screen = CODEX_DRAFT
    status, body = send(server, owner, "shared", "again", "prompt")
    assert status == 409 and "may be a draft" in body["error"]

    terminal.screen = CODEX_EMPTY
    terminal.last_owner_input_ms = int(time.time() * 1000)
    assert "typed in its terminal" in send(server, owner, "shared", "again", "prompt")[1]["error"]

    terminal.last_owner_input_ms = 0
    history.set_state("shared", "busy")
    assert "isn't idle" in send(server, owner, "shared", "again", "prompt")[1]["error"]
    assert len(terminal.pasted) == 1


def test_control_tower_reports_mail_nudges_backup_and_tokens(tower, monkeypatch, tmp_path) -> None:
    server, owner, _, history = tower
    from duckterm.core.tokens import TokenLedger

    server._tokens = TokenLedger(tmp_path / "no-claude", tmp_path / "no-codex")
    history.record(
        {
            "_id": "n1",
            "_ts": int(time.time() * 1000),
            "event_type": "OracleNudge",
            "session_key": "shared",
        }
    )
    status, body = dispatch(server, "GET", "/control-tower", {})
    assert status == 200
    assert body["mail"]["nudges"] == 1
    assert body["backup"] == {"destination": None, "status": None, "finished_at": None}
    assert body["tokens"]["by_agent"]["codex"]["output"] == 0
    assert body["remote"] == {"available": False, "count": 0}
