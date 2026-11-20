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
