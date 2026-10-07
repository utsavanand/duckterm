"""A new harness gets current assigned work without inheriting unrelated tasks."""

import asyncio
import json

import pytest
from tests.runtime import test_restart_harness_switch as fixtures

from duckterm import harness_switch

rig = fixtures.rig


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


def test_switch_seed_includes_owned_open_tasks_without_changing_them(rig, monkeypatch):
    server, _, _, _ = rig
    active, parked = seed_tasks(server)
    history = server.history
    before = history.folder_tasks.list_tasks()
    changes = history._conn.total_changes

    async def checkpoint(*args):
        return {"id": "checkpoint", "summary": "Completed checkpoint summary"}

    monkeypatch.setattr(server, "_create_checkpoint", checkpoint)
    prepared = asyncio.run(harness_switch.prepare(server, "a", history.session("a")))
    seed = prepared["seed"]
    for value in (active["id"], parked["id"], "Finish the release", "in_progress", "parked"):
        assert value in seed
    assert "publish after review" in seed
    assert "Wait for owner feedback" in seed
    assert "Completed checkpoint summary" in seed
    assert "NEW conversation" in seed
    for value in ("Already shipped", "Another agent owns this", "Outside the shared scope"):
        assert value not in seed
    assert history.folder_tasks.list_tasks() == before
    assert history._conn.total_changes == changes


def test_tasks_are_read_after_checkpoint_and_with_current_shared_scope(rig, monkeypatch):
    server, _, _, _ = rig
    active, parked = seed_tasks(server)
    history = server.history

    async def checkpoint(*args):
        with history._conn:
            history.folder_tasks.update(active["id"], {"status": "done"})
            history._conn.execute(
                "UPDATE folder_tasks SET owner_session='someone-else' WHERE id=?",
                (parked["id"],),
            )
        history.set_meta("a", group="NewScope")
        with history._conn:
            history.folder_tasks.start("NewScope", "a", "Current new assignment")
        return {"id": "checkpoint", "summary": "Summary"}

    monkeypatch.setattr(server, "_create_checkpoint", checkpoint)
    prepared = asyncio.run(harness_switch.prepare(server, "a", history.session("a")))
    assert "Current new assignment" in prepared["seed"]
    assert "Finish the release" not in prepared["seed"]
    assert "Revisit design" not in prepared["seed"]
    assert "Outside the shared scope" not in prepared["seed"]


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
        history.set_harness_identity("a", source, "claude", None)
        screen[0] = "────────────────\n❯ \n────────────────"
    launches = []

    async def checkpoint(*args):
        return {"id": "checkpoint", "summary": "Summary"}

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

    monkeypatch.setattr(server, "_create_checkpoint", checkpoint)
    monkeypatch.setattr(server.orchestrator, "launch", launch)

    async def run():
        await server.restarts.request("a", "", target)
        await hook(server, runtime=source)
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
