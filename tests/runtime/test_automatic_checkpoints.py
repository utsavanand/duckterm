"""Owner, 2026-11-15: messages written while automatic checkpoints were off stay a gap.

Automatic checkpoints skip them and record the gap once as its own entry; the
handoff declares it; a manual checkpoint still summarizes everything.
"""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from test_maintained_progress import provider
from test_memory import memory_rig, transcript

from duckterm import memory_checkpoint
from duckterm.core import memory_refresh

__all__ = ["memory_rig"]
REAL_RECONCILE = memory_refresh.reconcile_reply
OFF, ON = 1_794_700_000_000, 1_794_703_600_000  # one hour off


def message(path, text, at_ms):
    stamp = datetime.fromtimestamp(at_ms / 1000, UTC).isoformat().replace("+00:00", "Z")
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                {"type": "user", "timestamp": stamp, "message": {"role": "user", "content": text}}
            )
            + "\n"
        )


def settings(tmp, **value):
    path = tmp / "duckterm" / "checkpoint-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"automatic": True, "automatic_off": [[OFF, ON]], **value}))


def checkpoints(server, label):
    return [
        json.loads(r["record_json"])
        for r in server.history._conn.execute(
            "SELECT record_json FROM checkpoints WHERE session_key='agent' AND label=? "
            "ORDER BY created_at",
            (label,),
        )
    ]


def rig(memory_rig, monkeypatch):
    from duckterm import server as sm

    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Owner instruction before")
    message(path, "Written while automatic was off", OFF + 60_000)
    message(path, "Owner instruction after", ON + 60_000)
    calls = provider(monkeypatch)
    writer = sm.summarize

    def shaped(prompt, *, tier, claude_model=None, effort=None, schema=None):
        if "MEMORY UPDATE:\n" in prompt:
            return writer(prompt)
        return SimpleNamespace(text='{"ops": []}', failure_reason=None)

    monkeypatch.setattr(memory_refresh, "reconcile_reply", REAL_RECONCILE)
    monkeypatch.setattr(sm, "summarize", shaped)
    settings(tmp)
    return server, calls


def run(server, label):
    return asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), label))


def test_automatic_checkpoint_skips_the_gap_and_records_it_once(memory_rig, monkeypatch):
    server, calls = rig(memory_rig, monkeypatch)
    result = run(server, memory_checkpoint.AUTOMATIC_LABEL)
    assert result["record"]["summary_update"]["state"] == "updated"
    written = "\n".join(calls)
    assert "Owner instruction before" in written and "Owner instruction after" in written
    assert "Written while automatic was off" not in written
    (gap,) = checkpoints(server, memory_checkpoint.GAP_LABEL)
    assert gap["gap"]["periods"] == [[OFF, ON]] and gap["gap"]["records"] == 1
    # A later automatic checkpoint skips the same messages without a second entry.
    run(server, memory_checkpoint.AUTOMATIC_LABEL)
    assert len(checkpoints(server, memory_checkpoint.GAP_LABEL)) == 1
    assert "Written while automatic was off" not in "\n".join(calls)


def test_the_handoff_declares_the_gap_and_a_manual_checkpoint_fills_it(memory_rig, monkeypatch):
    from duckterm import memory_brief, memory_reconcile
    from duckterm.core import memory_folding

    server, calls = rig(memory_rig, monkeypatch)
    run(server, memory_checkpoint.AUTOMATIC_LABEL)
    catalog = server.memory.catalog("agent")
    sources, _ = server.memory.materialize(catalog)
    captured = asyncio.run(server.progress_coordinator.capture("agent"))
    scope = captured["source"]["scope_hash"]
    _, handoff, _ = memory_brief.assemble(
        "agent",
        sources,
        captured["required"],
        memory_folding.rows(server.digests, "agent", scope),
        {"session_key": "agent"},
        items=memory_reconcile.latest(server.digests, "agent", scope),
    )
    reasons = {o["reason"]: o["count"] for o in handoff["coverage"]["omitted"]}
    assert reasons == {"automatic_checkpoints_off": 1}
    # The owner's own checkpoint summarizes what automation left out.
    run(server, "manual")
    assert "Written while automatic was off" in "\n".join(calls)


NOW = 1_800_000_000_000


def agents(server, *keys, updated=NOW):
    for key in keys:
        server.history.record(
            {
                "_id": key,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "runtime": "claude-code",
                "session_id": key + "-native",
                "cwd": "/tmp",
            }
        )
    marks = ",".join("?" * len(keys))
    server.history._conn.execute(
        f"UPDATE sessions SET test=0, updated_at=? WHERE session_key IN ({marks})",
        (updated, *keys),
    )


def messages(server, key, count, last_ts):
    for i in range(count):
        server.history.record(
            {
                "_id": f"{key}-{i}",
                "_ts": last_ts - i,
                "session_key": key,
                "event_type": "UserPromptSubmit" if i % 2 else "Stop",
            }
        )


def test_due_after_fifty_messages_or_fifteen_quiet_minutes(memory_rig):
    from duckterm.core import auto_checkpoints as auto

    server = memory_rig[0]
    agents(server, "busy", "quiet", "recent", "idle")
    agents(server, "stale", updated=NOW - auto.ACTIVE_MS - 1)
    messages(server, "busy", 50, NOW - 1000)
    messages(server, "quiet", 3, NOW - auto.QUIET_MS)
    messages(server, "recent", 3, NOW - 60_000)
    # Idle for over 7 days: not caught up until it becomes active again.
    messages(server, "stale", 3, NOW - auto.ACTIVE_MS - 1000)
    conn = server.history._conn
    assert auto.due(conn, NOW, set()) == ["busy", "quiet"]
    assert auto.due(conn, NOW, {"busy"}) == ["quiet"]
    # Messages before the agent's last checkpoint, including a failed one, don't count.
    conn.execute(
        "INSERT INTO checkpoints "
        "(id,session_key,label,summary,record_json,markdown_path,created_at) "
        "VALUES ('c','quiet','Automatic','','{}',NULL,?)",
        (NOW - 1,),
    )
    assert auto.due(conn, NOW, set()) == ["busy"]


def test_fifty_messages_wait_for_a_pause(memory_rig):
    # Mid-turn the conversation file keeps changing while it is copied, and the
    # checkpoint failed (2026-11-25: 7 of 8 automatic failures).
    from duckterm.core import auto_checkpoints as auto

    server = memory_rig[0]
    agents(server, "working")
    messages(server, "working", 50, NOW - 5000)
    server.history.record(
        {"_id": "tool", "_ts": NOW - 1000, "session_key": "working", "event_type": "PreToolUse"}
    )
    conn = server.history._conn
    assert auto.due(conn, NOW, set()) == []
    assert auto.due(conn, NOW - 1000 + auto.SETTLED_MS, set()) == ["working"]
    server.history.record(
        {"_id": "end", "_ts": NOW, "session_key": "working", "event_type": "Stop"}
    )
    assert auto.due(conn, NOW + 1, set()) == ["working"]


def test_a_stale_saved_copy_is_not_checkpointed_as_current(memory_rig, monkeypatch):
    # When the live conversation can't be read, memory falls back to an older
    # saved copy. A checkpoint of it would present old work as current, and an
    # old-format copy crashed the checkpoint with a KeyError.
    from duckterm import memory_continuity

    server, calls = rig(memory_rig, monkeypatch)
    real = memory_continuity.capture

    async def fallback(server, key, captured):
        captured = await real(server, key, captured)
        captured["memory_sources"] = [
            {**s, "retained_fallback": True} if s["kind"] == "conversation" else s
            for s in captured["memory_sources"]
        ]
        return captured

    monkeypatch.setattr(memory_continuity, "capture", fallback)
    update = run(server, memory_checkpoint.AUTOMATIC_LABEL)["record"]["summary_update"]
    assert update["state"] == "failed" and update["reason"] == "source_changed"
    assert update["diagnostic"] == "freeze:memory_conflict"
    assert calls == []


def test_tick_honours_the_switch_two_at_a_time_and_pending_restarts(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    agents(server, "a", "b", "c", "d")
    for n, key in enumerate("abcd"):
        messages(server, key, 60 - n, NOW - 1000)
    started = []

    async def fake(key, row, label):
        started.append((key, label))
        await asyncio.sleep(0)

    monkeypatch.setattr(server, "_create_checkpoint", fake)
    monkeypatch.setattr(server.restarts, "pending", lambda key: key == "a")

    async def run():
        settings(tmp, automatic=False)
        assert server.auto_checkpoints.tick(NOW) == []
        settings(tmp)
        assert server.auto_checkpoints.tick(NOW) == ["b", "c"]
        assert server.auto_checkpoints.tick(NOW) == []  # both slots busy
        await asyncio.gather(*server.auto_checkpoints.running.values())
        await asyncio.sleep(0)
        assert server.auto_checkpoints.tick(NOW) == ["b", "c"]

    asyncio.run(run())
    assert started[:2] == [(k, memory_checkpoint.AUTOMATIC_LABEL) for k in "bc"]


def test_health_puts_problems_first_and_counts_gaps(memory_rig):
    from duckterm.core import checkpoint_health

    server = memory_rig[0]
    conn = server.history._conn
    agents(server, "current", "behind", "failing", "none")

    def checkpoint(key, at, state, label="manual", extra=None):
        record = {
            "summary_update": {"state": state, "reason": "source_unavailable"},
            **(extra or {}),
        }
        conn.execute(
            "INSERT INTO checkpoints "
            "(id,session_key,label,summary,record_json,markdown_path,created_at) "
            "VALUES (?,?,?,'',?,NULL,?)",
            (f"{key}-{at}", key, label, json.dumps(record), at),
        )

    hour = 3_600_000
    checkpoint("current", NOW - 5 * 60_000, "updated")
    checkpoint("behind", NOW - 2 * hour, "updated")
    checkpoint("behind", NOW - 3 * hour, None, memory_checkpoint.GAP_LABEL, {"gap": {"records": 4}})
    checkpoint("failing", NOW - 2 * hour, "updated")
    checkpoint("failing", NOW - hour, "failed")
    messages(server, "behind", 4, NOW - 60_000)
    messages(server, "none", 2, NOW - 60_000)
    report = {a["key"]: a for a in checkpoint_health.report(conn, NOW)}
    assert [a["key"] for a in checkpoint_health.report(conn, NOW)][:3] == [
        "failing",
        "none",
        "behind",
    ]
    assert {k: report[k]["status"] for k in ("current", "behind", "failing", "none")} == {
        "current": "current",
        "behind": "behind",
        "failing": "failing",
        "none": "none",
    }
    assert report["behind"]["new_messages"] == 4 and report["behind"]["gaps"] == 1
    assert report["failing"]["saved_at"] == NOW - 2 * hour
    assert report["failing"]["last_attempt"]["reason"] == "source_unavailable"


def test_health_is_owner_only(memory_rig):
    from test_session_api import dispatch

    server, headers, _, _ = memory_rig
    assert dispatch(server, "GET", "/checkpoint-health", {})[0] == 401
    assert dispatch(server, "GET", "/checkpoint-health", headers)[0] in {401, 403}
    status, body = dispatch(server, "GET", "/checkpoint-health", {"x-duckterm-token": server.token})
    # The rig's agents are test sessions, which the health view leaves out.
    assert (status, body) == (200, {"automatic": False, "agents": []})
