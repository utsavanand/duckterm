"""Preserve durable collaboration work across an explicit exhausted-agent switch."""

import asyncio

import pytest
from tests.runtime import test_restart_harness_switch as fixtures
from tests.runtime.test_restarts import drain, hook
from tests.runtime.test_session_api import call, enroll

from duckterm.persistence.artifacts import registration
from duckterm.persistence.history import HistoryStore

rig = fixtures.rig
offline = fixtures.offline


@pytest.mark.parametrize("target", ["codex", "claude-code"])
def test_immediate_switch_preserves_work_after_database_reopen(rig, monkeypatch, tmp_path, target):
    server, _, screen, calls = rig
    history = server.history
    source = "claude-code" if target == "codex" else "codex"
    history.set_harness_identity("a", source, source, None)
    screen[0] = (
        "Usage limit reached\n────────────────\n"
        + ("❯ " if source == "claude-code" else "› ")
        + "\n────────────────"
    )
    history.set_meta("a", name="Synthetic release owner", group="Team", notes="Keep saved notes")
    history.record(
        {"_id": "peer", "_ts": 1, "session_key": "peer", "event_type": "SessionStart", "test": True}
    )
    history.set_meta("peer", group="Team")
    owner = enroll(history, "a", "Team")
    peer = enroll(history, "peer", "Team")
    active = call(history, owner, "POST", "/tasks", {"title": "Finish current release"})[1]["task"]
    parked = call(history, owner, "POST", "/tasks", {"title": "Wait for design approval"})[1][
        "task"
    ]
    call(history, owner, "PATCH", f"/tasks/{parked['id']}", {"status": "parked"})
    questions = []
    for i in range(2):
        q = call(
            history,
            {**peer, "idempotency-key": f"qa-{i}"},
            "POST",
            "/questions",
            {"target_session_id": "a", "question": f"Pending QA item {i}"},
        )[1]
        questions.append(q["id"])
    call(history, owner, "POST", f"/questions/{questions[1]}/accept")
    report = tmp_path / "saved-report.md"
    report.write_text("# Synthetic saved evidence\n")
    artifact = history.artifacts.register("a", registration(report, "Evidence"))
    report.unlink()
    table_names = ("folder_tasks", "session_questions")

    def snapshot(store):
        result = {
            name: [tuple(row) for row in store._conn.execute(f"SELECT * FROM {name} ORDER BY 1")]
            for name in table_names
        }
        # Per-process credentials may rotate; collaboration scope and purpose must not.
        result["membership"] = [
            tuple(row)
            for row in store._conn.execute(
                "SELECT session_key, root, folder, root_mode, api_name, purpose, activity "
                "FROM session_api_members ORDER BY session_key"
            )
        ]
        return result

    before = snapshot(history)
    artifact_before = history.artifacts.get("a", artifact["id"])
    original = history.session("a")
    seeds = []

    async def checkpoint(*args):
        return {"id": "synthetic-checkpoint", "summary": "Completed work"}

    async def launch(**kwargs):
        seeds.append(kwargs["prompt"])
        assert kwargs["test"] is True and kwargs["session_key"] == "a"
        await hook(
            server,
            event_type="SessionStart",
            runtime=target,
            session_id="new-qa-id",
            launch_generation=kwargs["env"]["DUCKTERM_HARNESS_GENERATION"],
        )

    monkeypatch.setattr(server, "_create_checkpoint", checkpoint)
    monkeypatch.setattr(server.orchestrator, "launch", launch)

    async def run():
        assert not server.restarts.turn_finished("a")
        await server.restarts.request("a", "", target, interrupt=True)
        await drain(server)
        assert server.restarts.read("a")["status"] == "completed"

    asyncio.run(run())
    assert calls == [("stop", "a")]
    assert len(seeds) == 1 and active["id"] in seeds[0] and parked["id"] in seeds[0]
    assert snapshot(history) == before
    reopened = HistoryStore(tmp_path / "restart.sqlite")
    try:
        assert snapshot(reopened) == before
        assert reopened.artifacts.get("a", artifact["id"]) == artifact_before
        assert reopened.session_id_for("a") == "new-qa-id"
        row = reopened.session("a")
        for field in ("name", "grp", "cwd", "notes", "intention", "branch", "worktree_path"):
            assert row[field] == original[field]
        assert {r["session_key"] for r in reopened.sessions()} == {"a", "peer"}
    finally:
        reopened.close()
        history.purge_test_sessions()


@pytest.mark.parametrize("blocker", ["checkpoint_failure", "archived", "transfer"])
def test_checkpoint_boundary_failure_never_stops_source(rig, monkeypatch, blocker):
    server, _, _, calls = rig

    async def checkpoint(*args):
        if blocker == "checkpoint_failure":
            raise RuntimeError("synthetic checkpoint failure")
        if blocker == "archived":
            server.history.set_state("a", "archived", now=1)
        else:
            server._transfer_sources.add("a")
        return {"id": "checkpoint", "summary": "Summary"}

    monkeypatch.setattr(server, "_create_checkpoint", checkpoint)

    async def run():
        await server.restarts.request("a", "", "claude-code", interrupt=True)
        await drain(server)
        assert server.restarts.read("a")["status"] == "failed"
        assert not calls
        assert not server.restarts.interrupt_sources

    asyncio.run(run())
