"""A raised hand stays up until the owner attends to the session (owner
decision, 2026-09-30): the agent's own next event never lowers it."""

import json

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=history)
    server.bus.publish(
        {"event_type": "SessionStart", "session_key": "s", "runtime": "claude-code", "test": True}
    )
    yield server, history, {"x-duckterm-token": server.token}
    history.close()


def row(history):
    return history.session("s")


def test_hand_goes_up_on_waiting_and_stays_through_the_agents_next_events(world) -> None:
    server, history, _ = world
    server.bus.publish({"event_type": "PermissionRequest", "session_key": "s", "tool_name": "Bash"})
    raised = row(history)["attention_since"]
    assert raised and row(history)["state"] == "waiting"
    server.bus.publish({"event_type": "PreToolUse", "session_key": "s", "tool_name": "Bash"})
    server.bus.publish({"event_type": "Stop", "session_key": "s"})
    assert (row(history)["state"], row(history)["attention_since"]) == ("idle", raised)
    server.bus.publish({"event_type": "PermissionRequest", "session_key": "s", "tool_name": "Bash"})
    assert row(history)["attention_since"] == raised  # still the first unattended ask


def test_opening_the_session_lowers_the_hand_without_touching_its_state(world) -> None:
    server, history, owner = world
    server.bus.publish({"event_type": "PermissionRequest", "session_key": "s", "tool_name": "Bash"})
    before = row(history)
    assert dispatch(server, "POST", "/sessions/s/attended", {})[0] == 401
    assert dispatch(server, "POST", "/sessions/s/attended", owner)[0] == 200
    after = row(history)
    assert after["attention_since"] is None
    assert (after["state"], after["event_count"], after["updated_at"]) == (
        before["state"],
        before["event_count"],
        before["updated_at"],
    )
    assert dispatch(server, "POST", "/sessions/nope/attended", owner)[0] == 404


def test_deciding_the_approval_in_the_dashboard_lowers_the_hand(world) -> None:
    server, history, owner = world
    status, body = dispatch(
        server,
        "POST",
        "/approvals",
        owner,
        json.dumps(
            {"session_key": "s", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        ).encode(),
    )
    server.bus.publish({"event_type": "PermissionRequest", "session_key": "s", "tool_name": "Bash"})
    assert row(history)["attention_since"]
    decision = json.dumps({"decision": "approve"}).encode()
    assert dispatch(server, "POST", f"/approvals/{body['id']}/decide", owner, decision)[0] == 200
    assert row(history)["attention_since"] is None


def test_the_hand_survives_a_restart(world, tmp_path) -> None:
    server, history, _ = world
    server.bus.publish({"event_type": "PermissionRequest", "session_key": "s", "tool_name": "Bash"})
    raised = row(history)["attention_since"]
    reopened = HistoryStore(tmp_path / "db.sqlite")
    assert reopened.session("s")["attention_since"] == raised
    reopened.close()
