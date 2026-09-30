"""Priority owner broadcasts: pinned at turn ends until replied to, idle
agents reminded without the settle wait, never mid-turn, owner-only, and
honest per-recipient status (design addendum 3, 2026-09-30)."""

import asyncio
import hashlib
import json
import time
from urllib.parse import quote

import pytest
from tests.runtime.test_oracle import CLAUDE_EMPTY, CODEX_EMPTY, FakeSupervisor
from tests.runtime.test_session_api import call, dispatch, enroll

from duckterm.core import oracle
from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

STOP = {"event_type": "Stop", "stop_hook_active": False}


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    for key, runtime in [("claude", "claude-code"), ("peer", "claude-code"), ("codex", "codex")]:
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "test": True,
                "runtime": runtime,
            }
        )
        history.set_meta(key, name=key, group="work")
    creds = {key: enroll(history, key) for key in ("claude", "peer", "codex")}
    for key in creds:
        history.set_state(key, "busy")
    server = Server(history=history)
    owner = {"x-duckterm-token": server.token}
    yield history, server, owner, creds
    history.close()


def send(server, owner, text, key, priority=True):
    return dispatch(
        server,
        "POST",
        f"/folders/{quote('work', safe='')}/broadcast",
        owner,
        json.dumps({"text": text, "request_key": key, "priority": priority}).encode(),
    )


def notice(server, key="claude"):
    output = server._inbox_hook_output(dict(STOP), key)
    return output["hookSpecificOutput"]["additionalContext"] if output else None


def status(server, owner, key):
    body = dispatch(server, "GET", f"/broadcasts/{key}", owner)[1]
    return {r["session_id"]: r["status"] for r in body["recipients"]}


def test_a_peer_cannot_mark_a_message_priority(scenario) -> None:
    history, server, owner, creds = scenario
    with pytest.raises(APIError) as exc:
        call(
            history,
            creds["peer"],
            "POST",
            "/questions",
            {"target_session_id": "claude", "question": "urgent!", "priority": True},
        )
    assert exc.value.status == 400
    assert dispatch(server, "POST", "/folders/work/broadcast", {}, b'{"text": "x"}')[0] == 401


def test_busy_agent_gets_the_pin_at_turn_end_ahead_of_other_mail_until_it_replies(
    scenario,
) -> None:
    history, server, owner, creds = scenario
    send(server, owner, "Stop deploying, main is broken", "p1", priority=False)
    send(server, owner, "Freeze merges until I say so", "p2")
    assert status(server, owner, "p2")["claude"] == "pending next turn"
    first = notice(server)
    assert first.startswith("PRIORITY from the owner (1 open, newest first)")
    assert "Freeze merges until I say so" in first
    assert first.index("PRIORITY") < first.index("unread owner message")  # pinned ahead
    assert status(server, owner, "p2")["claude"] == "delivered"
    again = notice(server)
    assert again.startswith("PRIORITY") and "unread owner message" not in again  # re-noticed
    message_id = history.session_api.broadcast_status("p2", lambda k: True)["recipients"][0][
        "message_id"
    ]
    call(history, creds["claude"], "GET", "/inbox")  # reading alone doesn't acknowledge
    assert notice(server).startswith("PRIORITY")
    call(
        history,
        creds["claude"],
        "POST",
        f"/questions/{message_id}/answer",
        {"text": "Frozen."},
    )
    assert notice(server) is None
    assert status(server, owner, "p2")["claude"] == "acknowledged"


def test_several_priorities_render_as_one_block_newest_first(scenario) -> None:
    _, server, owner, _ = scenario
    send(server, owner, "First: pause releases", "a")
    time.sleep(0.002)
    send(server, owner, "Second: rotate the token", "b")
    text = notice(server)
    assert text.count("PRIORITY from the owner") == 1
    assert "(2 open, newest first)" in text
    assert text.index("Second: rotate the token") < text.index("First: pause releases")


def test_owner_cancel_retires_every_pin(scenario) -> None:
    _, server, owner, _ = scenario
    send(server, owner, "Pause everything", "c")
    assert notice(server).startswith("PRIORITY")
    body = dispatch(server, "DELETE", "/broadcasts/c", owner)[1]
    assert {r["status"] for r in body["recipients"]} == {"cancelled"}
    assert notice(server) is None
    assert dispatch(server, "DELETE", "/broadcasts/c", {})[0] == 401


def test_an_agent_without_priority_delivery_is_inbox_only_and_gets_no_pin(scenario) -> None:
    _, server, owner, _ = scenario
    send(server, owner, "Rebase on main", "d")
    assert status(server, owner, "d")["codex"] == "inbox only"
    assert notice(server, "codex") is None  # codex has no turn-end notice path


def test_open_priority_is_never_swept_but_a_replied_one_is(scenario, monkeypatch) -> None:
    history, server, owner, creds = scenario
    send(server, owner, "Keep this until answered", "e")
    ids = {
        r["session_id"]: r["message_id"]
        for r in history.session_api.broadcast_status("e", lambda k: True)["recipients"]
    }
    call(history, creds["claude"], "POST", f"/questions/{ids['claude']}/answer", {"text": "ok"})
    later = time.time() + 30 * 86400
    monkeypatch.setattr("duckterm.core.session_api.time.time", lambda: later)
    history.session_api.pending_counts()  # runs the sweep
    remaining = {
        r[0]
        for r in history._conn.execute(
            "SELECT recipient FROM session_questions WHERE kind = 'broadcast'"
        )
    }
    assert "claude" not in remaining and {"peer", "codex"} <= remaining


@pytest.mark.parametrize(
    ("priority", "since_stop_ms", "nudged"),
    [(True, 10_000, True), (False, 10_000, False), (False, oracle.SETTLE_MS, True)],
)
def test_idle_nudge_skips_the_settle_wait_only_for_priority(priority, since_stop_ms, nudged):
    now = 10_000_000
    mail = [
        {
            "id": "b-1",
            "kind": "broadcast",
            "status": "queued",
            "created_at": now - 1000,
            "last_read_at": 0,
            "priority": int(priority),
        }
    ]
    picked = oracle.should_nudge(
        state="idle",
        turn_ended_ms=now - since_stop_ms,
        last_owner_input_ms=0,
        prompt_empty=True,
        mail=mail,
        previous=None,
        now_ms=now,
    )
    assert bool(picked) is nudged


def test_idle_claude_is_reminded_at_once_with_the_owners_words(scenario, monkeypatch) -> None:
    history, server, owner, _ = scenario
    history.set_state("claude", "idle")
    history.record(
        {"_id": "s", "_ts": int(time.time() * 1000), "event_type": "Stop", "session_key": "claude"}
    )
    sup = FakeSupervisor(CLAUDE_EMPTY)
    codex = FakeSupervisor(CODEX_EMPTY)
    history.set_state("codex", "idle")
    history.record(
        {"_id": "c", "_ts": int(time.time() * 1000), "event_type": "Stop", "session_key": "codex"}
    )
    monkeypatch.setattr(
        server.orchestrator, "get", lambda key: {"claude": sup, "codex": codex}.get(key)
    )
    send(server, owner, "Stop and write a status update", "f")

    asyncio.run(server._oracle_tick())
    typed = b"".join(sup.pasted).decode()
    assert "PRIORITY from the owner" in typed and "Stop and write a status update" in typed
    assert codex.pasted == []  # inbox only: the usual settle wait applies
    assert status(server, owner, "f")["claude"] == "delivered"


@pytest.mark.parametrize("outcome", ["stuck", "failed"])
def test_a_nudge_that_did_not_submit_is_not_delivered(scenario, monkeypatch, outcome) -> None:
    history, server, owner, _ = scenario
    history.set_state("claude", "idle")
    history.record(
        {"_id": "s", "_ts": int(time.time() * 1000), "event_type": "Stop", "session_key": "claude"}
    )
    monkeypatch.setattr(
        server.orchestrator,
        "get",
        lambda key: FakeSupervisor(CLAUDE_EMPTY) if key == "claude" else None,
    )

    async def not_submitted(key, text):
        return outcome

    monkeypatch.setattr(server, "_submit_prompt", not_submitted)
    send(server, owner, "Stop and write a status update", "g")

    asyncio.run(server._oracle_tick())
    assert status(server, owner, "g")["claude"] == "pending next turn"
    assert notice(server).startswith("PRIORITY")  # the turn-end notice still carries it
    assert status(server, owner, "g")["claude"] == "delivered"


def test_a_plain_broadcast_keeps_the_hash_older_servers_stored(scenario) -> None:
    history, server, owner, _ = scenario
    send(server, owner, "Rebase on main", "h", priority=False)
    stored = history._conn.execute(
        "SELECT content_hash FROM session_broadcasts WHERE request_key = 'h'"
    ).fetchone()[0]
    assert stored == hashlib.sha256(json.dumps(["work", "Rebase on main"]).encode()).hexdigest()
    assert send(server, owner, "Rebase on main", "h", priority=False)[0] == 202  # a retry
    assert send(server, owner, "Rebase on main", "h")[0] == 409  # priority is different content
