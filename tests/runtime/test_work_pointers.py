"""Canonical tasks/mail remain readable without becoming summary or seed copies."""

import asyncio
import json

from test_maintained_progress import provider
from test_memory import memory_rig, transcript
from test_session_api import call

from duckterm import memory_continuity, memory_handoff
from duckterm.core.saved_progress import required_context

__all__ = ["memory_rig"]


def test_bounded_pointers_keep_assignment_counts_and_originals(memory_rig, monkeypatch):
    server, headers, peer, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep the original owner decision.")
    for n in range(45):
        task = server.history.folder_tasks.start("Tests", "agent", f"Task {n} " + "🔒" * 800)
        server.history.folder_tasks.update(task["id"], {"note": "taskbodyfixture " * 500})
    server.history.folder_tasks.start("Tests", "peer", "PEER TASK TITLE")
    server.history.folder_tasks.start("TestsOther", "agent", "OUTSIDE TASK TITLE")
    for n in range(12):
        call(
            server.history,
            {**peer, "idempotency-key": f"mail-{n}"},
            "POST",
            "/questions",
            {
                "target_session_id": "agent",
                "question": "mailbodyfixture " * 500,
            },
        )
        server.history._conn.execute("UPDATE session_questions SET created_at=1")
    server.history._conn.commit()
    before = server.history.folder_tasks.list_tasks()
    pointers = required_context(server, "agent", server.history.session("agent"))
    stats = pointers["work_references"]
    assert stats["assigned_open_tasks"] == 45
    assert stats["folder_open_tasks"] == 46
    assert stats["incoming_messages"] == 12
    assert len(pointers["tasks"]) == len(pointers["mail"]) == 8
    assert stats["omitted_task_pointers"] == 37 and stats["omitted_mail_pointers"] == 4
    assert len(json.dumps(pointers, ensure_ascii=False).encode()) < 9000
    assert all(item["title_truncated"] and "note" not in item for item in pointers["tasks"])
    assert all("question" not in item and "answer" not in item for item in pointers["mail"])
    calls = provider(monkeypatch)

    async def run():
        outcome = await server.progress_coordinator.refresh_result("agent")
        assert outcome.state == "updated"
        revision = outcome.revision
        checkpoint = await server._create_checkpoint(
            "agent", server.history.session("agent"), "manual"
        )
        assert len(checkpoint["record"]["mail_ids"]) == 12
        assert "source_changed" not in checkpoint["reason_codes"]
        assert "source_missing" not in checkpoint["reason_codes"]
        from duckterm.persistence.saved_state import retention_predicate

        expirable = server.history._conn.execute(
            "SELECT count(*) FROM session_questions q WHERE " + retention_predicate("mail")
        ).fetchone()[0]
        assert expirable == 0  # Even pointers omitted from the prompt remain pinned.
        captured = await server.progress_coordinator.capture("agent")
        captured = await memory_continuity.capture(server, "agent", captured)
        brief, coverage, _ = memory_handoff.assemble(
            "agent", captured["memory_sources"], captured["required"], revision
        )
        assert "duckterm session task list --folder Tests" in brief
        assert "duckterm session get <message-id>" in brief
        assert pointers["mail"][0]["id"] in brief
        for text in [*calls, brief]:
            assert "taskbodyfixture" not in text
            assert "mailbodyfixture" not in text
            assert "PEER TASK TITLE" not in text and "OUTSIDE TASK TITLE" not in text
        data = json.loads(calls[0].split("MEMORY UPDATE:\n", 1)[1])
        assert not any(r["role"] in ("task", "peer", "owner") for r in data["records"])
        for term in ("taskbodyfixture", "mailbodyfixture"):
            matches = await server.memory.operate("agent", "search", {"q": [term]})
            match = matches["results"][0]
            original = await server.memory.operate(
                "agent",
                "read",
                {
                    "source": [match["source"]],
                    "record": [match["record_id"]],
                    "offset": [str(match["offset"])],
                },
            )
            assert term in original["text"]
        assert revision["continuity"]["summary_input"]["remaining_eligible_records"] == 0
        assert coverage["summary_state"] == "partial"  # Raw bodies were not summarized.

    asyncio.run(run())
    assert server.history.folder_tasks.list_tasks() == before


def test_pointers_disappear_when_membership_is_stale(memory_rig):
    server, _, peer, _ = memory_rig
    server.history.folder_tasks.start("Tests", "agent", "Do not leak moved tasks")
    call(
        server.history,
        {**peer, "idempotency-key": "pending"},
        "POST",
        "/questions",
        {
            "target_session_id": "agent",
            "question": "Moved mail",
        },
    )
    server.history._conn.execute("UPDATE sessions SET grp='Elsewhere' WHERE session_key='agent'")
    value = required_context(server, "agent", server.history.session("agent"))
    assert value["tasks"] == [] and value["mail"] == []
    assert value["work_references"] == {"available": False}


def test_change_to_mail_beyond_pointer_limit_invalidates_checkpoint_hash(memory_rig, monkeypatch):
    server, _, peer, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep owner rules.")
    for n in range(9):
        call(
            server.history,
            {**peer, "idempotency-key": f"boundary-{n}"},
            "POST",
            "/questions",
            {
                "target_session_id": "agent",
                "question": f"Assignment {n}",
            },
        )
    provider(monkeypatch)
    from duckterm.persistence.saved_state import resolve_checkpoint

    async def run():
        saved = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        before = await server.progress_coordinator.capture("agent")
        omitted = saved["record"]["mail_ids"][-1]
        assert omitted not in {r["id"] for r in before["facts"]["required"]["mail"]}
        with server.history._conn:
            server.history._conn.execute(
                "UPDATE session_questions SET question='Changed content' WHERE id=?", (omitted,)
            )
        after = await server.progress_coordinator.capture("agent")
        assert before["required"] == after["required"]
        assert before["facts"] != after["facts"]
        assert before["source"]["mail_hash"] != after["source"]["mail_hash"]
        raw = json.loads(
            server.history._conn.execute(
                "SELECT record_json FROM checkpoints WHERE id=?", (saved["id"],)
            ).fetchone()[0]
        )
        assert (
            "source_changed"
            in resolve_checkpoint(server.history._conn, "agent", {**saved, "record": raw})[
                "reason_codes"
            ]
        )

    asyncio.run(run())
