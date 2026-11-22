"""A new harness gets current assigned work without inheriting unrelated tasks."""

import asyncio
import json

import pytest
from tests.runtime import test_restart_harness_switch as fixtures

rig = fixtures.rig
offline = fixtures.offline


def seed_tasks(server):
    history = server.history
    history.set_meta("a", group="Team/Current")
    with history._conn:
        tasks = history.folder_tasks
        active = tasks.start("Team/Current", "a", "Finish the release")
        tasks.update(active["id"], {"note": "Candidate is verified; publish after review"})
        parked = tasks.start("Team", "a", "Revisit design")
        tasks.update(parked["id"], {"status": "parked", "note": "Wait for owner feedback"})
        done = tasks.start("Team/Current", "a", "Already shipped")
        tasks.update(done["id"], {"status": "done"})
        tasks.start("Team/Current", "someone-else", "Another agent owns this")
        tasks.start("TeamOther", "a", "Outside the shared scope")
    return active, parked


def test_switch_seed_includes_owned_open_tasks_without_changing_them(rig):
    server, _, _, _ = rig
    active, parked = seed_tasks(server)
    history = server.history

    async def run():
        proof = await fixtures.prepare(server)
        before = history.folder_tasks.list_tasks()
        changes = history._conn.total_changes
        prepared = await server.memory_preparation.prepared("a", proof, "claude-code", "")
        assert history.folder_tasks.list_tasks() == before
        assert history._conn.total_changes == changes
        return prepared["seed"]

    seed = asyncio.run(run())
    for value in (active["id"], parked["id"], "Finish the release", "in_progress", "parked"):
        assert value in seed
    assert "publish after review" not in seed
    assert "Wait for owner feedback" not in seed
    assert "duckterm session task list" in seed
    for value in ("Already shipped", "Another agent owns this", "Outside the shared scope"):
        assert value not in seed


def test_task_context_is_bounded_and_calls_out_omissions(rig):
    server, _, _, _ = rig
    history = server.history
    history.set_meta("a", group="Team")
    with history._conn:
        for i in range(15):
            task = history.folder_tasks.start("Team", "a", f"Task {i:02d} " + "T" * 16000)
            history.folder_tasks.update(task["id"], {"note": "N" * 16000})
            history._conn.execute(
                "UPDATE folder_tasks SET created_at=? WHERE id=?", (i, task["id"])
            )
    context = history.folder_tasks.handoff_context("a")
    assert len(context) <= 6000
    assert "Task 00" in context and "Task 10" not in context
    shown = [json.loads(line) for line in context.splitlines() if line.startswith("{")]
    assert 1 <= len(shown) <= 10
    assert "truncated" in context and "More tasks" in context
    assert "T" * 201 not in context and "N" * 301 not in context


def test_task_context_rejects_stale_membership_and_scope_prefix_collision(rig):
    server, _, _, _ = rig
    seed_tasks(server)
    history = server.history
    with history._conn:
        history._conn.execute("UPDATE sessions SET grp='Moved' WHERE session_key='a'")
    assert history.folder_tasks.handoff_context("a") == ""


@pytest.mark.parametrize("source,target", [("codex", "claude-code"), ("claude-code", "codex")])
def test_switch_launch_gets_task_context_and_leaves_task_ownership_intact(
    rig, monkeypatch, source, target
):
    from tests.runtime.test_restarts import drain, hook

    server, _, screen, _ = rig
    active, parked = seed_tasks(server)
    history = server.history
    if source == "claude-code":
        fixtures.claude_source(server, screen)
    launches = []

    async def launch(**kwargs):
        launches.append(kwargs)
        await hook(
            server,
            event_type="SessionStart",
            runtime=target,
            session_id="new-id",
            launch_generation=kwargs["env"]["DUCKTERM_HARNESS_GENERATION"],
        )
        return "a"

    monkeypatch.setattr(server.orchestrator, "launch", launch)

    async def run():
        await fixtures.switch(server, "", target)
        await drain(server)

    asyncio.run(run())
    assert server.restarts.read("a")["status"] == "completed"
    assert len(launches) == 1 and launches[0]["test"] is True
    assert active["id"] in launches[0]["prompt"] and parked["id"] in launches[0]["prompt"]
    assert history.folder_tasks.get(active["id"])["owner_session"] == "a"
    assert history.folder_tasks.get(parked["id"])["status"] == "parked"
    previous = server.restarts.read("a")["previous_conversation"]
    assert "seed" not in previous
    assert "Finish the release" not in json.dumps(previous)
