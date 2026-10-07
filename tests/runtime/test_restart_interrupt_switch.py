"""An explicit harness switch can stop an exhausted source without a Stop hook."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from tests.runtime import test_restart_harness_switch as fixtures
from tests.runtime.test_restarts import drain, hook
from tests.runtime.test_session_api import dispatch

from duckterm.core.session_api import APIError
from duckterm.restarts import Restarts

rig = fixtures.rig


@pytest.fixture(autouse=True)
def checkpoint(monkeypatch):
    async def create(*args):
        return {"id": "checkpoint", "summary": "Latest completed work"}

    monkeypatch.setattr("duckterm.server.Server._create_checkpoint", create)


@pytest.mark.parametrize("source,target", [("codex", "claude-code"), ("claude-code", "codex")])
def test_explicit_switch_executes_without_any_source_stop_hook(rig, source, target):
    server, _, screen, calls = rig
    server.history.set_harness_identity("a", source, source, None)
    if source == "claude-code":
        screen[0] = "────────────────\n❯ \n────────────────"

    async def run():
        assert not server.restarts.turn_finished("a")
        await server.restarts.request("a", "", target, interrupt=True)
        await drain(server)
        assert server.restarts.read("a")["status"] == "completed"
        assert [call[0] for call in calls] == ["stop", "launch"]
        assert server.history.session("a")["runtime"] == target

    asyncio.run(run())


def test_default_switch_still_waits_and_immediate_same_harness_is_refused(rig):
    server, _, _, calls = rig

    async def run():
        with pytest.raises(APIError):
            await server.restarts.request("a", "", "codex", interrupt=True)
        for invalid in ("true", 1, None):
            with pytest.raises(APIError):
                await server.restarts.request("a", "", "claude-code", interrupt=invalid)
        result = await server.restarts.request("a", "", "claude-code")
        assert result["status"] == "queued" and not calls
        with pytest.raises(APIError):
            await server.restarts.request("a", "", "claude-code", interrupt=True)
        server.restarts.cancel("a")

    asyncio.run(run())


@pytest.mark.parametrize("change", ["hook", "input", "draft", "process", "cancel", "identity"])
def test_change_during_checkpoint_preserves_source_without_delayed_retry(rig, monkeypatch, change):
    server, sup, screen, calls = rig

    async def create(*args):
        if change == "hook":
            await hook(server, event_type="UserPromptSubmit")
        elif change == "input":
            sup.last_owner_input_ms = 1
        elif change == "draft":
            screen[0] = "────────────────\n› Owner draft\n────────────────"
        elif change == "process":
            monkeypatch.setattr(
                server.orchestrator, "get", lambda key: SimpleNamespace(running=True)
            )
        elif change == "cancel":
            server.restarts.cancel("a")
        else:
            server.history.set_restart_control(
                "a",
                {
                    **server.restarts.read("a"),
                    "native_observation": {
                        "runtime": "codex",
                        "native_id": "new-source-id",
                        "ts": 9999999999999,
                    },
                },
            )
        return {"id": "checkpoint", "summary": "Summary"}

    monkeypatch.setattr(server, "_create_checkpoint", create)

    async def run():
        await server.restarts.request("a", "", "claude-code", interrupt=True)
        if change == "cancel":
            await asyncio.gather(*list(server.restarts.tasks.values()), return_exceptions=True)
        else:
            await drain(server)
        assert not calls
        assert server.restarts.read("a")["status"] in {"failed", "canceled"}
        assert server.history.session("a")["runtime"] == "codex"
        await hook(server)
        await drain(server)
        assert not calls

    asyncio.run(run())


def test_activity_during_target_probe_refuses_immediate_request(rig, monkeypatch):
    server, _, _, calls = rig

    async def version(_):
        await hook(server, event_type="UserPromptSubmit")
        return "claude 1.0.0"

    monkeypatch.setattr("duckterm.restarts.cli_version", version)

    async def run():
        with pytest.raises(APIError):
            await server.restarts.request("a", "", "claude-code", interrupt=True)
        assert not calls and not server.restarts.pending("a")

    asyncio.run(run())


def test_server_restart_does_not_replay_an_immediate_switch(rig, monkeypatch):
    server, _, _, calls = rig
    monkeypatch.setattr(server.restarts, "schedule", lambda key: None)

    async def run():
        await server.restarts.request("a", "", "claude-code", interrupt=True)
        assert server.restarts.read("a")["status"] == "queued"
        server.restarts = Restarts(server)
        assert server.restarts.read("a")["status"] == "failed"
        await hook(server)
        await drain(server)
        assert not calls

    asyncio.run(run())


def test_owner_endpoint_forwards_explicit_mode_and_advertises_capability(rig, monkeypatch):
    server, _, _, _ = rig
    captured = []

    async def request(key, model, harness=None, *, interrupt=False):
        captured.append((key, model, harness, interrupt))
        return {"status": "queued"}

    monkeypatch.setattr(server.restarts, "request", request)
    data = json.dumps({"harness": "claude-code", "model": "", "interrupt": True}).encode()
    assert dispatch(server, "POST", "/sessions/a/restart", {}, data)[0] == 401
    assert not captured
    assert (
        dispatch(server, "POST", "/sessions/a/restart", {"x-duckterm-token": server.token}, data)[0]
        == 202
    )
    assert captured == [("a", "", "claude-code", True)]
    assert asyncio.run(server.restarts.options("a"))["supports_interrupt_switch"] is True


def test_replaced_process_before_execution_is_never_stopped(rig, monkeypatch):
    server, sup, _, calls = rig
    schedule = server.restarts.schedule
    monkeypatch.setattr(server.restarts, "schedule", lambda key: None)

    async def run():
        await server.restarts.request("a", "", "claude-code", interrupt=True)
        replacement = SimpleNamespace(
            running=True,
            last_owner_input_ms=sup.last_owner_input_ms,
            visible_screen=sup.visible_screen,
        )
        monkeypatch.setattr(server.orchestrator, "get", lambda key: replacement)
        schedule("a")
        await drain(server)
        assert not calls
        assert server.restarts.read("a")["status"] == "failed"
        assert not server.restarts.interrupt_sources

    asyncio.run(run())
