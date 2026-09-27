"""Collaboration tracks outcomes after mail closes, without expanding agent scope."""

import json

import pytest
from tests.runtime.test_session_api import call, dispatch, enroll

from duckterm.core import oracle
from duckterm.core.session_api import APIError
from duckterm.core.work_items import HOUR
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    store = HistoryStore(tmp_path / "db.sqlite")
    for key in "abcd":
        store.record(
            {"_id": key, "_ts": 1, "session_key": key, "event_type": "SessionStart", "test": True}
        )
        store.set_meta(key, group="work/" + key if key != "d" else "outside")
    auth = {k: enroll(store, k, "work" if k != "d" else "outside") for k in "abcd"}
    yield store, auth
    store.purge_test_sessions()
    store.close()


def request(store, auth, *, sender="a", target="b", parent=None, title="Fix the bug"):
    req = {"target_session_id": target, "question": "An implementation assignment"}
    if title is not None:
        req["work_title"] = title
    if parent:
        req["parent_request_id"] = parent
    return call(
        store,
        {**auth[sender], "idempotency-key": f"{sender}-{target}-{title}"},
        "POST",
        "/questions",
        req,
    )[1]


def accept(store, auth, q):
    return call(store, auth[q["recipient"]], "POST", f"/questions/{q['id']}/accept")[1]


def update(store, auth, work, req, who="b"):
    return call(store, auth[who], "PATCH", f"/work/{work['id']}", req)[1]


def test_accept_creates_once_reply_does_not_complete_and_restart_preserves(scenario, tmp_path):
    store, auth = scenario
    q = request(store, auth)
    work = accept(store, auth, q)["work"]
    assert work["state"] == "accepted"
    assert accept(store, auth, q)["work"]["id"] == work["id"]
    call(store, auth["b"], "POST", f"/questions/{q['id']}/answer", {"text": "Acknowledged"})
    assert store.session_api.work.get("b", work["id"])["state"] == "accepted"
    polled = call(store, auth["a"], "GET", f"/questions/{q['id']}")[1]
    assert polled["status"] == "answered" and polled["work"]["state"] == "accepted"
    second = HistoryStore(tmp_path / "db.sqlite")
    try:
        assert second.session_api.work.get("b", work["id"])["state"] == "accepted"
    finally:
        second.close()
    done = update(store, auth, work, {"state": "done", "evidence": "Commit abcdef123"})
    assert done["closed_at"] and done["evidence"] == "Commit abcdef123"
    assert store.session_api.work.listing("a")["work"][0]["state"] == "done"


def test_plain_questions_do_not_create_fake_work_and_accept_can_mark_existing(scenario):
    store, auth = scenario
    q = request(store, auth, title=None)
    assert "work" not in accept(store, auth, q)
    assert store.session_api.work.listing("b")["work"] == []
    marked = call(
        store,
        auth["b"],
        "POST",
        f"/questions/{q['id']}/accept",
        {"work_title": "Implement agreed fix"},
    )[1]
    assert marked["work"]["state"] == "accepted"


def test_completion_and_blocked_need_concrete_references(scenario):
    store, auth = scenario
    work = accept(store, auth, request(store, auth))["work"]
    for req in (
        {"state": "done"},
        {"state": "done", "evidence": "trust me"},
        {"state": "blocked"},
        {"state": "dropped"},
    ):
        with pytest.raises(APIError) as exc:
            update(store, auth, work, req)
        assert exc.value.status == 400
    blocked = update(
        store, auth, work, {"state": "blocked", "blocker": "Owner must choose destination"}
    )
    assert blocked["blocker"]
    assert not store.session_api.work.nudge_items("b", blocked["updated_at"] + 2 * HOUR)
    active = update(store, auth, work, {"state": "in_progress", "note": "Destination selected"})
    assert active["blocker"] == ""
    assert len(active["history"]) == 3


def test_forward_and_decline_preserve_work(scenario):
    store, auth = scenario
    q = request(store, auth)
    work = accept(store, auth, q)["work"]
    forwarded = update(store, auth, work, {"owner_session": "c", "note": "Needs UI fix"})
    assert forwarded["owner_session"] == "c" and forwarded["state"] == "proposed"
    assert store.session_api.work.listing("c")["work"][0]["id"] == work["id"]
    assert store.session_api.work.nudge_items("c", forwarded["updated_at"] + 6 * 60_000)
    # An old recipient closing the old message must not undo a subsequent handoff.
    call(store, auth["b"], "POST", f"/questions/{q['id']}/decline", {"text": "Forwarded to UI"})
    current = store.session_api.work.get("a", work["id"])
    assert current["owner_session"] == "c"


def test_decline_without_forward_returns_to_proposed(scenario):
    store, auth = scenario
    q = request(store, auth)
    work = accept(store, auth, q)["work"]
    call(store, auth["b"], "POST", f"/questions/{q['id']}/decline", {"text": "Cannot handle this"})
    saved = store.session_api.work.get("a", work["id"])
    assert saved["owner_session"] is None and saved["state"] == "proposed"


def test_scope_and_owner_authorization_cannot_be_forged(scenario):
    store, auth = scenario
    work = accept(store, auth, request(store, auth))["work"]
    for who in "cd":
        with pytest.raises(APIError) as exc:
            call(store, auth[who], "GET", f"/work/{work['id']}")
        assert exc.value.status == 404
    with pytest.raises(APIError) as exc:
        update(store, auth, work, {"owner_authorized": True})
    assert exc.value.status == 403
    with pytest.raises(APIError):
        update(store, auth, work, {"owner_session": "d", "note": "outside scope"})
    server = Server(history=store)
    assert dispatch(server, "GET", "/work", {})[0] == 401
    assert dispatch(server, "GET", "/work", auth["b"])[0] == 403
    status, body = dispatch(
        server,
        "PATCH",
        f"/work/{work['id']}",
        {"x-duckterm-token": server.token},
        json.dumps({"owner_authorized": True}).encode(),
    )
    assert status == 200 and body["owner_authorized"] == 1


def test_status_receipts_once_read_does_not_create_receipts_and_chain_is_private(scenario):
    store, auth = scenario
    parent = request(store, auth, title="Coordinate fix")
    call(store, auth["b"], "GET", "/inbox")
    assert store.session_api.work.updates("a") == []
    accept(store, auth, parent)
    accept(store, auth, parent)
    assert len(store.session_api.work.updates("a")) == 1
    child = request(store, auth, sender="b", target="c", parent=parent["id"], title="Private child")
    work = accept(store, auth, child)["work"]
    store.session_api.work.updates("a")
    store.session_api.work.updates("b")
    update(
        store,
        auth,
        work,
        {
            "state": "done",
            "evidence": "https://github.com/test/repo/pull/42",
            "note": "Private child details",
        },
        who="c",
    )
    ancestor = store.session_api.work.updates("a")
    assert len(ancestor) == 1 and ancestor[0]["downstream"] is True
    assert ancestor[0]["kind"] == "work_done"
    assert "Private child" not in json.dumps(ancestor)
    assert store.session_api.work.get("b", work["id"])["state"] == "done"
    assert store.session_api.work.listing("a")["work"][0]["state"] == "accepted"


def test_parent_scope_and_closed_parent_rejected(scenario):
    store, auth = scenario
    q = request(store, auth)
    with pytest.raises(APIError):
        request(store, auth, sender="c", target="b", parent=q["id"], title="Bad parent")
    call(store, auth["a"], "POST", f"/questions/{q['id']}/cancel")
    with pytest.raises(APIError):
        request(store, auth, sender="b", target="c", parent=q["id"], title="Closed parent")


def test_stale_work_survives_reply_and_sends_honest_hourly_updates(scenario, monkeypatch):
    store, auth = scenario
    now = [10 * HOUR]
    monkeypatch.setattr("duckterm.core.work_items.time.time", lambda: now[0] / 1000)
    q = request(store, auth)
    work = accept(store, auth, q)["work"]
    call(store, auth["b"], "POST", f"/questions/{q['id']}/answer", {"text": "On it"})
    store.session_api.work.updates("a")
    assert not store.session_api.work.nudge_items("b", now[0])
    now[0] += HOUR + 1
    picked = store.session_api.work.nudge_items("b", now[0])
    assert len(picked) == 1
    assert "continue your work" in oracle.reminder(picked, now[0])
    assert "Fix the bug" not in oracle.reminder(picked, now[0])
    store.session_api.work.tick(now[0])
    store.session_api.work.tick(now[0])
    updates = store.session_api.work.updates("a")
    assert len(updates) == 1 and updates[0]["kind"] == "stale"
    assert "No new progress" in updates[0]["note"]
    assert store.session_api.turn_end_notice("b")
    assert store.session_api.turn_end_notice("b") is None
    old = store.session_api.work.get("b", work["id"])["updated_at"]
    update(store, auth, work, {"state": "accepted"})
    assert store.session_api.work.get("b", work["id"])["updated_at"] == old
    update(store, auth, work, {"state": "in_progress", "note": "Reproduced and fixing"})
    assert not store.session_api.work.nudge_items("b", now[0])


def test_deleted_assignee_does_not_delete_outcome_and_test_cleanup_is_complete(scenario):
    store, auth = scenario
    work = accept(store, auth, request(store, auth))["work"]
    store.delete_session("b")
    saved = store.session_api.work.get("a", work["id"])
    assert saved["state"] == "proposed" and saved["owner_session"] is None
    store.purge_test_sessions()
    for table in ("session_work", "session_work_events", "session_work_updates"):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_work_nudge_cooldown_survives_restart_and_does_not_repeat_at_hour_boundary(
    scenario, tmp_path, monkeypatch
):
    store, auth = scenario
    now = [10 * HOUR]
    monkeypatch.setattr("duckterm.core.work_items.time.time", lambda: now[0] / 1000)
    work = accept(store, auth, request(store, auth))["work"]
    now[0] += HOUR + HOUR // 2
    mail = store.session_api.work.nudge_items("b", now[0])
    assert len(mail) == 1
    store.session_api.work.notified(mail, now[0])
    second = HistoryStore(tmp_path / "db.sqlite")
    try:
        assert second.session_api.work.turn_notice("b") is None
        assert second.session_api.work.nudge_items("b", now[0] + HOUR // 2) == []
        assert second.session_api.work.nudge_items("b", now[0] + HOUR) == []
        later = now[0] + 4 * HOUR
        retry = second.session_api.work.nudge_items("b", later)
        assert len(retry) == 1
        second.session_api.work.notified(retry, later)
        assert second.session_api.work.nudge_items("b", later + 100 * HOUR) == []
        assert any(
            u.get("kind") == "attention_needed" for u in second.session_api.work.updates("a")
        )
        now[0] = later + 100 * HOUR
        assert second.session_api.work.turn_notice("b") is None
        update(store, auth, work, {"state": "in_progress", "note": "Resumed"})
        assert len(second.session_api.work.nudge_items("b", now[0] + HOUR)) == 1
    finally:
        second.close()
    done = update(store, auth, work, {"state": "done", "evidence": "v0.4.70"})
    assert update(store, auth, work, {"state": "done", "evidence": "v0.4.70"}) == done


def test_scope_move_unassigns_instead_of_losing_work(scenario):
    store, auth = scenario
    work = accept(store, auth, request(store, auth))["work"]
    store.set_meta("b", group="outside")
    store.session_api.work.tick(work["updated_at"] + HOUR)
    row = store.session_api.work.get(None, work["id"])
    assert row["state"] == "proposed" and row["owner_session"] is None
    with pytest.raises(APIError):
        store.session_api.work.get("b", work["id"])


def test_parent_walk_is_bounded_and_tolerates_broken_or_cyclic_links(scenario):
    store, auth = scenario
    chain = []
    for i in range(8):
        chain.append(
            request(
                store,
                auth,
                sender="abc"[i % 3],
                target="abc"[(i + 1) % 3],
                parent=chain[-1]["id"] if chain else None,
                title=f"Step {i}",
            )
        )
    accept(store, auth, chain[-1])
    updates = [u for key in "abc" for u in store.session_api.work.updates(key)]
    assert len(updates) == 5
    # A malformed persisted cycle cannot loop forever or duplicate receipts.
    store._conn.execute(
        "UPDATE session_questions SET parent_request_id=id WHERE id=?", (chain[-1]["id"],)
    )
    store.session_api.work.request_update(chain[-1]["id"], "cycle_probe", 1)
    assert sum(len(store.session_api.work.updates(k)) for k in "abc") == 1
    store._conn.execute(
        "UPDATE session_questions SET parent_request_id='missing' WHERE id=?", (chain[-1]["id"],)
    )
    store.session_api.work.request_update(chain[-1]["id"], "missing_probe", 1)
    assert sum(len(store.session_api.work.updates(k)) for k in "abc") == 1


def test_cli_work_and_parent_options_round_trip_over_real_http(scenario, monkeypatch, capsys):
    import asyncio

    from duckterm import session_client
    from duckterm.cli import build_parser

    store, auth = scenario
    q = request(store, auth)
    server = Server(history=store)

    async def run():
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        monkeypatch.setattr(
            session_client,
            "client_credentials",
            lambda: (
                f"http://127.0.0.1:{port}",
                auth["b"]["authorization"].removeprefix("Bearer "),
            ),
        )

        async def command(args):
            parsed = build_parser().parse_args(["session", *args])
            assert await asyncio.to_thread(session_client.main, parsed) == 0
            return json.loads(capsys.readouterr().out)

        try:
            accepted = await command(["accept", q["id"]])
            work = accepted["work"]
            child = await command(
                [
                    "ask",
                    "c",
                    "Implement the child task",
                    "--work-title",
                    "Child outcome",
                    "--parent-request",
                    q["id"],
                    "--request-key",
                    "cli-child",
                ]
            )
            assert child["parent_request_id"] == q["id"]
            assert child["work_title"] == "Child outcome"
            await command(["reply", q["id"], "--file", str(reply_file)])
            assert (await command(["work", "list"]))["work"][0]["state"] == "accepted"
            progress = await command(
                [
                    "work",
                    "update",
                    work["id"],
                    "--state",
                    "in_progress",
                    "--note",
                    "Fix implemented",
                ]
            )
            assert progress["state"] == "in_progress"
            blocked = await command(
                ["work", "update", work["id"], "--state", "blocked", "--blocker", "Awaiting review"]
            )
            assert blocked["blocker"] == "Awaiting review"
            done = await command(
                ["work", "update", work["id"], "--state", "done", "--evidence", "abcdef123"]
            )
            assert done["state"] == "done"
            assert (await command(["work", "get", work["id"]]))["evidence"] == "abcdef123"
        finally:
            listener.close()
            await listener.wait_closed()

    reply_file = store.session_api.credential_dir.parent / "test-reply.txt"
    reply_file.write_text("Acknowledged, still working")
    asyncio.run(run())


def test_real_oracle_tick_nudges_stale_outcomes_after_reply_without_peer_text(
    scenario, monkeypatch
):
    import asyncio
    import time

    from tests.runtime.test_oracle import CODEX_DRAFT, CODEX_EMPTY, FakeSupervisor

    store, auth = scenario
    now = [int(time.time() * 1000)]
    monkeypatch.setattr("duckterm.core.work_items.time.time", lambda: now[0] / 1000)
    q = request(store, auth, title="UNTRUSTED PRIVATE TASK TEXT")
    work = accept(store, auth, q)["work"]
    call(store, auth["b"], "POST", f"/questions/{q['id']}/answer", {"text": "Acknowledged"})
    store.record(
        {
            "_id": "stopped-turn",
            "_ts": now[0] - 2 * HOUR,
            "event_type": "Stop",
            "session_key": "b",
            "test": True,
        }
    )
    store._conn.execute("UPDATE sessions SET runtime='codex' WHERE session_key='b'")
    store._conn.execute(
        "UPDATE session_work SET updated_at=? WHERE id=?", (now[0] - 2 * HOUR, work["id"])
    )
    store._conn.commit()
    server = Server(history=store)
    sup = FakeSupervisor(CODEX_DRAFT)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: sup if key == "b" else None)
    asyncio.run(server._oracle_tick())
    assert not sup.pasted  # a stale task never overwrites a draft
    sup.screen = CODEX_EMPTY
    asyncio.run(server._oracle_tick())
    assert len(sup.pasted) == 2
    text = sup.pasted[0].decode()
    assert "duckterm session work list" in text and "PRIVATE TASK" not in text
    assert sup.pasted[1] == b"\r"
    # Even a fresh server's empty in-memory nudge cache cannot spam the same work.
    server = Server(history=store)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: sup if key == "b" else None)
    asyncio.run(server._oracle_tick())
    assert len(sup.pasted) == 2
    now[0] += HOUR
    store.record(
        {
            "_id": "busy-turn",
            "_ts": now[0],
            "event_type": "PreToolUse",
            "session_key": "b",
            "test": True,
        }
    )
    asyncio.run(server._oracle_tick())
    assert len(sup.pasted) == 2  # busy work still gets no terminal interruption


def test_work_instructions_match_cli_states_and_persistent_request_default():
    from duckterm.cli import build_parser
    from duckterm.core.work_items import STATES
    from duckterm.helpers.session_instructions import GUIDE

    parser = build_parser()
    ask_args = parser.parse_args(["session", "ask", "peer", "Question"])
    assert ask_args.timeout == 0
    assert "Requests persist by default" in GUIDE
    assert "A reply closes the conversation, not its work item" in GUIDE
    for state in STATES:
        args = parser.parse_args(["session", "work", "update", "w-example", "--state", state])
        assert args.state == state


@pytest.mark.parametrize("title", [None, "Fix the bug"])
def test_accept_reconciles_precreated_work_and_retries_preserve_progress(scenario, title):
    store, auth = scenario
    q = request(store, auth, title=title)
    work = call(
        store, auth["a"], "POST", "/work", {"origin_request_id": q["id"], "title": "Explicit work"}
    )[1]
    assert work["state"] == "proposed"
    accepted = accept(store, auth, q)["work"]
    assert accepted["id"] == work["id"] and accepted["state"] == "accepted"
    assert len(accept(store, auth, q)["work"]["history"]) == 2
    update(store, auth, work, {"state": "in_progress"})
    assert accept(store, auth, q)["work"]["state"] == "in_progress"


def test_accept_old_request_does_not_reclaim_forwarded_precreated_work(scenario):
    store, auth = scenario
    q = request(store, auth)
    work = call(
        store, auth["a"], "POST", "/work", {"origin_request_id": q["id"], "title": "Explicit work"}
    )[1]
    update(store, auth, work, {"owner_session": "c", "note": "Handoff"}, who="a")
    accepted = accept(store, auth, q)
    assert accepted["status"] == "accepted" and "work" not in accepted
    current = store.session_api.work.get("c", work["id"])
    assert current["state"] == "proposed" and current["owner_session"] == "c"
    assert "work" not in accept(store, auth, q)


@pytest.mark.parametrize("version", [3, 4])
def test_existing_database_migrates_work_without_losing_columns(tmp_path, monkeypatch, version):
    import sqlite3

    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    path = tmp_path / "db.sqlite"
    old = HistoryStore(path)
    old.close()
    with sqlite3.connect(path) as conn:
        for table in ("session_work", "session_work_events", "session_work_updates"):
            conn.execute(f"DROP TABLE {table}")
        for column in ("parent_request_id", "work_title"):
            conn.execute(f"ALTER TABLE session_questions DROP COLUMN {column}")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "pinned" in columns:
            conn.execute("ALTER TABLE sessions DROP COLUMN pinned")
        if version == 4:
            conn.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        conn.execute(f"PRAGMA user_version={version}")
        original_columns = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    migrated = HistoryStore(path)
    try:
        assert migrated._conn.execute("PRAGMA user_version").fetchone()[0] == 5
        columns = {r["name"] for r in migrated._conn.execute("PRAGMA table_info(sessions)")}
        assert original_columns <= columns
        if version == 4:
            assert "pinned" in columns
        question_columns = {
            r["name"] for r in migrated._conn.execute("PRAGMA table_info(session_questions)")
        }
        assert {"parent_request_id", "work_title"} <= question_columns
        assert "notice_count" in {
            r["name"] for r in migrated._conn.execute("PRAGMA table_info(session_work)")
        }
        assert migrated.session_api.work.listing(None)["work"] == []
    finally:
        migrated.close()


def test_work_maintenance_runs_with_oracle_disabled(scenario, monkeypatch):
    import asyncio

    store, _ = scenario
    server = Server(history=store)
    monkeypatch.setenv("DUCKTERM_ORACLE", "off")
    calls = []
    monkeypatch.setattr(store.session_api.work, "tick", lambda now: calls.append(now))
    monkeypatch.setattr(store, "sweep_dead", lambda *args, **kwargs: [])
    monkeypatch.setattr(store, "live_watched", lambda: [])
    count = 0

    async def sleep(_):
        nonlocal count
        count += 1
        if count > 3:
            raise asyncio.CancelledError

    async def forbidden():
        pytest.fail("Oracle must stay disabled")

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(server, "_oracle_tick", forbidden)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(server._sweep_dead_loop())
    assert len(calls) == 1
