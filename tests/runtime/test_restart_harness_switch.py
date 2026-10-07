"""Resume identity must not gate switching to a new harness conversation."""

import asyncio
import json
from pathlib import Path

import pytest
from tests.runtime.test_codex_resume_identity import A
from tests.runtime.test_restarts import drain, hook
from tests.runtime.test_restarts import rig as restart_rig
from tests.runtime.test_session_api import Writer, dispatch

from duckterm.core.session_api import APIError
from duckterm.model_catalog import CatalogError
from duckterm.persistence.history import HistoryStore


@pytest.fixture
def rig(tmp_path, monkeypatch):
    for value in restart_rig.__wrapped__(tmp_path, monkeypatch):

        async def version(binary):
            return "codex 0.155.1" if binary == "codex" else binary + " 1.0.0"

        monkeypatch.setattr("duckterm.restarts.cli_version", version)
        yield value


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    async def choices(self, name):
        if name == "copilot":
            raise CatalogError("not reported")
        return [{"id": name + "-model", "label": name + " model"}]

    async def version(binary):
        return "codex 0.155.1" if binary == "codex" else binary + " 1.0.0"

    monkeypatch.setattr("duckterm.model_catalog.ModelCatalog.choices", choices)
    monkeypatch.setattr("duckterm.restarts.cli_version", version)
    monkeypatch.setattr(
        "duckterm.persistence.checkpoints.summarize",
        lambda _: type("Summary", (), {"text": "Checkpoint summary"})(),
    )


def test_unknown_resume_identity_still_offers_switch(rig, monkeypatch):
    server, _, _, calls = rig
    monkeypatch.setattr(server.history, "session_id_for", lambda _: None)
    status, result = dispatch(server, "GET", "/sessions/a/restart-options", {})
    assert status == 200
    assert not result["resume_restart"]["available"]
    assert "Cannot verify" in result["resume_restart"]["reason"]
    targets = {h["name"]: h for h in result["harnesses"]}
    assert targets["claude-code"]["available"]
    assert targets["claude-code"]["context"] == "seeded_new_conversation"
    assert not targets["codex"]["available"]
    assert targets["claude-code"]["models"][0]["id"] == "claude-code-model"
    assert targets["claude-code"]["model_source"] == "harness-reported"
    assert not targets["copilot"]["model_selection"]["available"]
    assert not calls


def test_unsupported_source_and_missing_target_have_independent_reasons(rig, monkeypatch):
    server, _, _, _ = rig
    server.history.set_harness_identity("a", "copilot", "copilot", None)
    monkeypatch.setattr(
        "duckterm.restarts.shutil.which", lambda name: None if name == "codex" else "/bin/claude"
    )
    result = asyncio.run(server.restarts.options("a"))
    assert not result["resume_restart"]["available"]
    choices = {h["name"]: h for h in result["harnesses"]}
    assert choices["claude-code"]["available"]
    assert not choices["codex"]["available"]
    assert "not installed" in choices["codex"]["reason"]


def test_common_guard_disables_every_path(rig):
    server, _, _, calls = rig
    server.history.set_state("a", "stopped", now=1)
    result = asyncio.run(server.restarts.options("a"))
    assert not result["resume_restart"]["available"]
    assert all(not h["available"] for h in result["harnesses"])
    assert "Resume" in result["reason"]
    assert not calls


def test_daemon_codex_target_is_unavailable_before_identity_dependency(rig, monkeypatch):
    server, _, _, calls = rig
    server.history.set_harness_identity("a", "claude-code", "claude", None)

    async def version(_):
        return "codex-cli 0.159.3"

    monkeypatch.setattr("duckterm.restarts.cli_version", version)

    async def run():
        choices = {h["name"]: h for h in (await server.restarts.options("a"))["harnesses"]}
        assert not choices["codex"]["available"]
        assert "shared-daemon" in choices["codex"]["reason"]
        with pytest.raises(APIError, match="shared-daemon"):
            await server.restarts.request("a", "", "codex")
        assert not calls

    asyncio.run(run())


def test_switch_preserves_card_and_records_new_identity_with_seed(rig, monkeypatch, tmp_path):
    server, _, _, calls = rig
    server.history.set_meta("a", name="Keep name", group="Projects", notes="Keep notes")
    server.history.set_intention("a", "Keep task")
    old_row = server.history.session("a")
    original_schema = server.history._conn.execute("PRAGMA user_version").fetchone()[0]
    launches = []

    async def launch(**kwargs):
        assert server.history.checkpoints("a")
        assert server.history.session_id_for("a") is None
        assert kwargs["session_key"] == "a"
        assert kwargs["cwd"] == old_row["cwd"]
        assert kwargs["record_intention"] is False
        assert kwargs["test"] is True
        assert "Checkpoint summary" in kwargs["prompt"]
        assert "Keep notes" in kwargs["prompt"]
        assert "NEW conversation" in kwargs["prompt"]
        launches.append(kwargs)
        server.bus.publish(
            {
                "event_type": "SessionStart",
                "session_key": "a",
                "runtime": "claude-code",
                "test": True,
            }
        )
        await hook(
            server,
            event_type="SessionStart",
            runtime="claude-code",
            session_id="new-native-id",
            launch_generation=kwargs["env"]["DUCKTERM_HARNESS_GENERATION"],
        )
        return "a"

    monkeypatch.setattr(server.orchestrator, "launch", launch)

    async def run():
        await server.restarts.request("a", "claude-model", "claude-code")
        assert not calls and not launches
        # The old conversation's transcript can disappear without disabling a new conversation.
        from duckterm.runtimes.codex import CodexRuntime

        path = CodexRuntime().locate_transcript(cwd=Path(old_row["cwd"]), session_id=A)
        path.unlink()
        await hook(server)
        await drain(server)
        state = server.restarts.read("a")
        assert state["status"] == "completed", state
        assert calls == [("stop", "a")]
        assert len(launches) == 1
        argv = launches[0]["runtime"].launch_command(
            cwd=Path(old_row["cwd"]), session_key="a", initial_prompt=""
        )
        assert argv == ["claude", "--model", "claude-model"]
        row = server.history.session("a")
        for field in (
            "session_key",
            "cwd",
            "grp",
            "name",
            "notes",
            "intention",
            "worktree_path",
            "branch",
        ):
            assert row.get(field) == old_row.get(field)
        assert row["runtime"] == "claude-code"
        assert server.history.session_id_for("a") == "new-native-id"
        assert state["previous_conversation"]["native_id"] == A
        assert state["previous_conversation"]["runtime"] == "codex"
        assert any(e["event_type"] == "HarnessSwitched" for e in server.history.events_for("a"))
        # Late old hooks and a second native start cannot hijack the switched card.
        await hook(server, event_type="SessionStart", runtime="codex", session_id=A)
        await hook(server, event_type="SessionStart", runtime="claude-code", session_id="wrong-id")
        assert server.history.session_id_for("a") == "new-native-id"
        assert server.history.session("a")["runtime"] == "claude-code"
        assert server.history._conn.execute("PRAGMA user_version").fetchone()[0] == original_schema
        reopened = HistoryStore(tmp_path / "restart.sqlite")
        assert reopened.session_id_for("a") == "new-native-id"
        reopened.close()

    asyncio.run(run())


def test_failed_switch_keeps_old_conversation_resumable(rig, monkeypatch):
    server, _, _, calls = rig
    old = server.history.session("a")
    server.restarts.save("a", configured_model="working-model")

    async def fail(**kwargs):
        # A spawn failure can already have emitted the new runtime's start.
        server.bus.publish(
            {
                "event_type": "SessionStart",
                "session_key": "a",
                "runtime": "claude-code",
                "test": True,
            }
        )
        raise ValueError("failed to spawn")

    monkeypatch.setattr(server.orchestrator, "launch", fail)

    async def run():
        await server.restarts.request("a", "new-model", "claude-code")
        await hook(server)
        await drain(server)
        assert calls == [("stop", "a")]
        state = server.restarts.read("a")
        assert state["status"] == "failed"
        assert "failed to spawn" in state["error"]
        assert state["configured_model"] == "working-model"
        assert server.history.session_id_for("a") == A
        row = server.history.session("a")
        assert row["runtime"] == old["runtime"] and row["state"] == "stopped"
        assert server._resume_argv("a", "codex", row)[0][-2:] == ["resume", A]

    asyncio.run(run())


def test_input_during_checkpoint_never_stops_old_agent(rig, monkeypatch):
    server, _, _, calls = rig

    async def checkpoint(*args):
        await hook(server, event_type="UserPromptSubmit")
        return {"id": "checkpoint", "summary": "notes"}

    monkeypatch.setattr(server, "_create_checkpoint", checkpoint)

    async def run():
        await server.restarts.request("a", "", "claude-code")
        await hook(server)
        await drain(server)
        assert not calls
        assert server.restarts.read("a")["status"] == "queued"
        server.restarts.cancel("a")
        await hook(server)
        assert not calls

    asyncio.run(run())


def test_post_validates_target_and_model_without_side_effects(rig):
    server, _, _, calls = rig
    for body in (
        {"harness": "shell"},
        {"harness": []},
        {"harness": "copilot", "model": "unsupported"},
    ):
        status, _ = dispatch(
            server,
            "POST",
            "/sessions/a/restart",
            {"x-duckterm-token": server.token},
            json.dumps(body).encode(),
        )
        assert status == 400
        assert not calls
    status, _ = dispatch(server, "POST", "/sessions/a/restart", {}, b'{"harness":"claude-code"}')
    assert status == 401
    status, _ = dispatch(
        server, "GET", "/sessions/a/restart-options", {"authorization": "Bearer peer"}
    )
    assert status == 403


def test_hook_identity_stays_unknown_until_matching_native_start(rig):
    server, _, _, _ = rig
    server.restarts.save(
        "a", native_binding={"runtime": "claude-code", "native_id": None, "retired_ids": [A]}
    )
    assert server.history.session_id_for("a") is None

    async def run():
        for data in (
            {"event_type": "SessionStart", "runtime": "claude-code", "session_id": A},
            {
                "event_type": "SessionStart",
                "runtime": "claude-code",
                "session_id": "new",
                "hook_host": "daemon",
            },
            {
                "event_type": "SessionStart",
                "runtime": "claude-code",
                "session_id": "new",
                "agent_id": "child",
            },
            {"event_type": "Stop", "runtime": "claude-code", "session_id": "new"},
        ):
            await server._ingest(Writer(), json.dumps({"session_key": "a", **data}).encode())
            assert server.history.session_id_for("a") is None
        await hook(server, event_type="SessionStart", runtime="claude-code", session_id="new")
        assert server.history.session_id_for("a") == "new"

    asyncio.run(run())


def test_old_generation_cannot_bind_or_control_new_launch(rig):
    server, _, _, _ = rig
    server.restarts.save(
        "a", native_binding={"runtime": "claude-code", "native_id": None, "generation": "current"}
    )

    async def run():
        await hook(
            server,
            event_type="SessionStart",
            runtime="claude-code",
            session_id="old",
            launch_generation="previous",
        )
        assert server.history.session_id_for("a") is None
        await hook(
            server,
            event_type="SessionStart",
            runtime="claude-code",
            session_id="new",
            launch_generation="current",
        )
        assert server.history.session_id_for("a") == "new"
        await hook(server, runtime="claude-code", session_id="new", launch_generation="previous")
        assert not server.restarts.turn_finished("a")
        await hook(server, runtime="claude-code", session_id="new", launch_generation="current")
        assert server.restarts.turn_finished("a")

    asyncio.run(run())


def test_pending_switch_duplicate_is_idempotent_but_different_target_conflicts(rig):
    server, _, _, calls = rig

    async def run():
        first = await server.restarts.request("a", "", "claude-code")
        again = await server.restarts.request("a", "", "claude-code")
        assert first["id"] == again["id"]
        with pytest.raises(APIError, match="already pending"):
            await server.restarts.request("a", "", "copilot")
        server.restarts.cancel("a")
        await hook(server)
        assert not calls

    asyncio.run(run())


@pytest.mark.parametrize("with_jq", [False, True])
def test_real_hook_forwards_switch_generation_without_network(tmp_path, with_jq):
    import os
    import shutil
    import subprocess
    import sys
    import time

    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("cat", "basename", "grep", "head", "sed"):
        (tools / name).symlink_to(shutil.which(name))
    if with_jq:
        jq = shutil.which("jq")
        if not jq:
            pytest.skip("jq is not installed")
        (tools / "jq").symlink_to(jq)
    capture = tmp_path / "payload.json"
    curl = tools / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import sys\nfrom pathlib import Path\n"
        f"Path({str(capture)!r}).write_text(sys.argv[sys.argv.index('-d')+1])\n"
    )
    curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tools),
        "DUCKTERM_HOME": str(tmp_path / "duckterm"),
        "DUCKTERM_SESSION_KEY": "synthetic-test",
        "DUCKTERM_HARNESS_GENERATION": "b" * 32,
    }
    env.pop("DUCKTERM_INTERNAL", None)
    hook_script = Path(__file__).resolve().parents[2] / "src/duckterm/hooks/duckterm-hook.sh"
    subprocess.run(
        ["/bin/bash", str(hook_script), "SessionStart", "claude-code"],
        input=json.dumps({"session_id": "synthetic-native", "cwd": str(tmp_path)}),
        text=True,
        env=env,
        check=True,
        capture_output=True,
        timeout=5,
    )
    for _ in range(100):
        if capture.exists() and capture.stat().st_size:
            break
        time.sleep(0.01)
    payload = json.loads(capture.read_text())
    assert payload["launch_generation"] == "b" * 32
    assert payload["session_id"] == "synthetic-native"
    assert payload["session_key"] == "synthetic-test"


def test_seeded_queue_can_learn_initial_id_from_real_parent_turn_end(rig):
    server, _, _, calls = rig
    server.history._conn.execute(
        "DELETE FROM events WHERE json_extract(payload_json, '$.session_id') IS NOT NULL"
    )
    # This fixture models an identity never captured, not telemetry retention.
    control = server.history.restart_control("a")
    control.pop("native_observation", None)
    server.history.set_restart_control("a", control)
    server.history._conn.commit()
    assert server.history.session_id_for("a") is None

    async def run():
        await server.restarts.request("a", "", "claude-code")
        assert not calls
        await hook(server, runtime="codex")
        await drain(server)
        assert calls[0] == ("stop", "a")
        assert server.restarts.read("a")["status"] == "completed"

    asyncio.run(run())


@pytest.mark.parametrize("startup_failure", [False, True])
def test_failed_switch_drains_delayed_eof_before_restoring(rig, monkeypatch, startup_failure):
    from duckterm.core.orchestrator import Orchestrator, SessionSupervisor

    server, original, _, calls = rig
    old = server.history.session("a")
    captured = []
    old_stop = server.orchestrator.stop
    real_stop = SessionSupervisor.stop
    orchestrator = server.orchestrator

    async def run():
        release_eof = asyncio.Event()
        draining = asyncio.Event()

        async def start(supervisor):
            supervisor._emit("SessionStart", command="claude --model invalid")

            async def output():
                await release_eof.wait()
                captured.append("final output")
                supervisor._emit("SessionEnd")

            supervisor._task = asyncio.create_task(output())
            if startup_failure:
                raise ValueError("failure after output task creation")

        async def settle(supervisor):
            draining.set()
            await real_stop(supervisor)

        async def stop(key):
            if key in orchestrator._supervisors:
                return await Orchestrator.stop(orchestrator, key)
            return await old_stop(key)

        monkeypatch.setattr(SessionSupervisor, "start", start)
        monkeypatch.setattr(SessionSupervisor, "stop", settle)
        monkeypatch.setattr(orchestrator, "launch", Orchestrator.launch.__get__(orchestrator))
        monkeypatch.setattr(
            orchestrator, "get", lambda key: orchestrator._supervisors.get(key, original)
        )
        monkeypatch.setattr(orchestrator, "stop", stop)
        monkeypatch.setattr(orchestrator, "_write_summary", lambda key: None)
        await server.restarts.request("a", "invalid", "claude-code")
        await hook(server)
        await asyncio.wait_for(draining.wait(), 5)
        # Neither a dead process nor startup failure is safe to roll back until EOF.
        assert server.history.session("a")["runtime"] == "claude-code"
        assert server.history.session_id_for("a") is None
        assert not captured
        release_eof.set()
        await drain(server)
        assert captured == ["final output"]
        assert server.restarts.read("a")["status"] == "failed"
        row = server.history.session("a")
        assert row["runtime"] == old["runtime"]
        assert row["command"] == old["command"]
        assert row["model"] == old["model"]
        assert row["state"] == "stopped"
        assert server.history.session_id_for("a") == A
        assert server._resume_argv("a", "codex", row)[0][-2:] == ["resume", A]
        assert calls == [("stop", "a")]
        if startup_failure:
            assert "a" not in orchestrator._supervisors

    asyncio.run(run())


def test_exact_resume_rotates_switched_card_generation(rig, monkeypatch):
    from duckterm.harness_switch import accept_hook

    server, _, _, _ = rig
    server.restarts.save(
        "a", native_binding={"runtime": "codex", "native_id": A, "generation": "old-generation"}
    )
    launches = []

    async def launch(**kwargs):
        launches.append(kwargs)

    monkeypatch.setattr(server.orchestrator, "launch", launch)
    status, _ = asyncio.run(server._resume_session("a", exact=True))
    assert status == 200
    generation = launches[0]["env"]["DUCKTERM_HARNESS_GENERATION"]
    assert generation and generation != "old-generation"
    assert server.history.session_id_for("a") == A
    event = {"event_type": "Stop", "session_key": "a", "session_id": A, "runtime": "codex"}
    assert not accept_hook(server, {**event, "launch_generation": "old-generation"})
    assert accept_hook(server, {**event, "launch_generation": generation})


def test_cancelled_partial_launch_drains_owned_supervisor(rig, monkeypatch):
    from duckterm.core.orchestrator import Orchestrator, SessionSupervisor
    from duckterm.runtimes.claude_code import ClaudeCodeRuntime

    server, _, _, _ = rig
    orchestrator = server.orchestrator
    real_stop = SessionSupervisor.stop

    async def run():
        started = asyncio.Event()
        draining = asyncio.Event()
        release_eof = asyncio.Event()
        completed = []

        async def start(supervisor):
            async def output():
                await release_eof.wait()
                completed.append("final output")
                supervisor._emit("SessionEnd")

            supervisor._task = asyncio.create_task(output())
            started.set()
            await asyncio.Event().wait()

        async def stop(supervisor):
            draining.set()
            await real_stop(supervisor)

        monkeypatch.setattr(SessionSupervisor, "start", start)
        monkeypatch.setattr(SessionSupervisor, "stop", stop)
        task = asyncio.create_task(
            Orchestrator.launch(
                orchestrator,
                runtime=ClaudeCodeRuntime("claude"),
                cwd=server.history.session("a")["cwd"],
                session_key="a",
                test=True,
            )
        )
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        await asyncio.wait_for(draining.wait(), 5)
        assert not completed and not task.done()
        release_eof.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed == ["final output"]
        assert "a" not in orchestrator._supervisors

    asyncio.run(run())
