"""A session can say it needs the owner in plain words (publish --needs-owner):
Oracle's Needs you lists it and its hand goes up, like an open question
(product assignment, 2026-10-05)."""

import json

import pytest
from tests.runtime.test_session_api import dispatch, enroll

from duckterm.cli import build_parser
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    history.record(
        {
            "_id": "s",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "ui",
            "test": True,
            "runtime": "codex",
        }
    )
    history.set_meta("ui", name="ui-dev", group="work")
    creds = enroll(history, "ui")
    yield Server(history=history), creds, history
    history.close()


def publish(server, creds, **body):
    return dispatch(server, "PATCH", "/api/v1/session/self", creds, json.dumps(body).encode())


def flagged(server):
    notes = dispatch(server, "GET", "/relay", {})[1]["notes"]
    return [
        (n["status"], n["question"], n.get("urgency"))
        for n in notes
        if n.get("source") == "needs_owner"
    ]


def test_needs_owner_lists_the_session_and_raises_its_hand(world) -> None:
    server, creds, history = world
    code, card = publish(server, creds, needs_owner="Approve pushing the timeline preview")
    assert (code, card["needs_owner"]) == (200, "Approve pushing the timeline preview")
    assert flagged(server) == [("open", "Approve pushing the timeline preview", "blocked")]
    assert dispatch(server, "GET", "/relay/count", {})[1] == {"open": 1}
    assert history.session("ui")["attention_since"] is not None

    publish(server, creds, needs_owner="Actually: choose between two layouts")
    assert [f[:2] for f in flagged(server)] == [
        ("handled", "Approve pushing the timeline preview"),
        ("open", "Actually: choose between two layouts"),
    ]

    publish(server, creds, needs_owner=None)  # --clear
    assert [f[0] for f in flagged(server)] == ["handled", "handled"]
    assert history.session("ui")["attention_since"] is None


def test_an_owner_reply_closes_it(world) -> None:
    server, creds, _ = world
    publish(server, creds, needs_owner="Which database should I use?")
    server.bus.publish(
        {"event_type": "UserPromptSubmit", "session_key": "ui", "prompt": "Postgres"}
    )
    assert [f[0] for f in flagged(server)] == ["handled"]


def test_clearing_keeps_the_hand_of_a_session_that_is_waiting_anyway(world) -> None:
    server, creds, history = world
    publish(server, creds, needs_owner="Approve the command")
    history.set_state("ui", "waiting")
    publish(server, creds, needs_owner=None)
    assert history.session("ui")["attention_since"] is not None


def test_the_flag_is_one_line_of_bounded_text(world) -> None:
    server, creds, _ = world
    assert publish(server, creds, needs_owner="x" * 501)[0] == 413  # the API's size refusal
    assert publish(server, creds, needs_owner="")[0] == 400
    assert flagged(server) == []


def test_the_cli_sends_the_flag_and_clear() -> None:
    parser = build_parser()
    args = parser.parse_args(["session", "publish", "--needs-owner", "Need a decision"])
    assert (args.needs_owner, args.clear) == ("Need a decision", False)
    assert parser.parse_args(["session", "publish", "--clear"]).clear is True
    with pytest.raises(SystemExit):
        parser.parse_args(["session", "publish", "--needs-owner", "x", "--clear"])
