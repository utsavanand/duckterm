"""Daemon-hosted Codex events reach the right session, or are parked; never
filed under a default ("Design — Shared-daemon identity", 2026-10-01). These
are main-qa's three routing checks for PR #187, which failed with "dropped:
not a Duckterm-launched session" before the resolver."""

import hashlib
import json

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

# Real 0.159.3 thread ids from the 2026-10-01 probe.
NATIVE_A = "01a0f8d6-7060-7c82-ba0b-8d34abe7ed53"
NATIVE_B = "01a0f8d6-997e-77f2-a031-581a09e22d50"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    for key in ("cx-a", "cx-b"):
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
    yield Server(history=history)
    history.close()


def launch_prompt(key: str) -> str:
    # The shape session_instructions.introduction() gives every Codex launch.
    digest = hashlib.sha256(key.encode()).hexdigest()
    return (
        "Duckterm session capability:\nRead the instruction file at "
        f'"/home/.duckterm/session-instructions/{digest}/collaboration.md".'
    )


def post(server, event_type, native, **extra):
    event = {"event_type": event_type, "session_id": native, "hook_host": "daemon",
             "runtime": "codex", **extra}  # fmt: skip
    headers = {"x-duckterm-token": server.token}
    return dispatch(server, "POST", "/events", headers, json.dumps(event).encode())[1]


def filed(server, key):
    rows = server.history._conn.execute(
        "SELECT event_type FROM events WHERE session_key = ? AND (event_type = 'NativeBound' "
        "OR json_extract(payload_json, '$.hook_host') = 'daemon') ORDER BY rowid",
        (key,),
    ).fetchall()
    return [r[0] for r in rows]


def test_two_sessions_under_one_daemon_each_get_their_own_events(server) -> None:
    post(server, "UserPromptSubmit", NATIVE_A, prompt=launch_prompt("cx-a"))
    post(server, "UserPromptSubmit", NATIVE_B, prompt=launch_prompt("cx-b"))
    for event_type in ("PreToolUse", "PostToolUse", "Stop"):
        post(server, event_type, NATIVE_B, tool_name="Bash")
        post(server, event_type, NATIVE_A, tool_name="Bash")
    expected = ["NativeBound", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]
    assert filed(server, "cx-a") == expected
    assert filed(server, "cx-b") == expected
    assert server.native_identity.status() == {"parked": 0, "parked_total": 0}


def test_an_unknown_native_id_is_parked_never_filed(server) -> None:
    """The 0.159 bug as a permanent regression: an event nobody can attribute
    used to land under whichever session started the daemon."""
    assert post(server, "PreToolUse", "01a0f8d2-unknown", tool_name="Bash") == {
        "parked": "unattributed daemon event"
    }
    post(server, "UserPromptSubmit", "01a0f8d2-unknown", prompt="a prompt with no launch marker")
    assert filed(server, "cx-a") == filed(server, "cx-b") == []
    assert dispatch(server, "GET", "/hooks/parked", {})[1] == {"parked": 2, "parked_total": 2}


def test_a_late_bind_claims_what_was_parked_and_restart_keeps_the_bind(server, tmp_path) -> None:
    post(server, "SessionStart", NATIVE_A)  # arrives before the launch prompt
    post(server, "UserPromptSubmit", NATIVE_A, prompt=launch_prompt("cx-a"))
    assert filed(server, "cx-a") == ["NativeBound", "SessionStart", "UserPromptSubmit"]
    assert server.native_identity.status()["parked"] == 0

    restarted = Server(history=server.history)  # bindings are rebuilt from history
    post(restarted, "Stop", NATIVE_A)
    assert filed(server, "cx-a")[-1] == "Stop"


def test_a_daemon_events_pid_and_session_state_are_left_alone(server) -> None:
    post(server, "UserPromptSubmit", NATIVE_A, prompt=launch_prompt("cx-a"), agent_pid=4242)
    payloads = [
        json.loads(r[0])
        for r in server.history._conn.execute(
            "SELECT payload_json FROM events WHERE session_key = 'cx-a'"
        )
    ]
    assert all("agent_pid" not in p for p in payloads)
    assert server.history.session("cx-a")["last_event_type"] == "UserPromptSubmit"


def test_an_id_that_only_ever_came_through_a_hook_is_not_trusted(server) -> None:
    """Before the fix, a daemon hook could file another session's events (and
    native id) under the first launcher. Such history must not become a bind;
    an id DuckTerm recorded itself may (main-qa's routing checks)."""
    stale = {"_id": "h1", "_ts": 2, "event_type": "Stop", "session_key": "cx-a",
             "session_id": NATIVE_B, "hook_event": True}  # fmt: skip
    server.history.record(stale)
    assert post(server, "Stop", NATIVE_B) == {"parked": "unattributed daemon event"}

    server.history.record(
        {
            "_id": "s2",
            "_ts": 3,
            "event_type": "SessionStart",
            "session_key": "cx-b",
            "session_id": NATIVE_B,
            "runtime": "codex",
        }  # fmt: skip
    )
    assert post(server, "Stop", NATIVE_B)["session_key"] == "cx-b"


def test_a_relaunch_may_take_a_new_native_id(server) -> None:
    """Bind once per launch (main-qa's edge cases), but a fresh launch of the
    same session (a server-published SessionStart) starts a new thread."""
    post(server, "UserPromptSubmit", NATIVE_A, prompt=launch_prompt("cx-a"))
    server.history.record(
        {"_id": "relaunch", "_ts": 2**41, "event_type": "SessionStart", "session_key": "cx-a",
         "runtime": "codex"}  # fmt: skip
    )
    assert (
        post(server, "UserPromptSubmit", NATIVE_B, prompt=launch_prompt("cx-a"))["session_key"]
        == "cx-a"
    )
    assert post(server, "Stop", NATIVE_A) == {"parked": "unattributed daemon event"}
