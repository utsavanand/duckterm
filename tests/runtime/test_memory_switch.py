"""Exercise preparation proofs through the existing stop/launch boundary."""

import asyncio
import json
from pathlib import Path

import pytest
from tests.runtime import test_restart_harness_switch as switch_fixtures
from tests.runtime.test_restarts import drain, hook
from tests.runtime.test_session_api import Writer, dispatch

from duckterm import memory_summary
from duckterm.core.session_api import APIError
from duckterm.memory_preparation import generation

rig = switch_fixtures.rig


@pytest.fixture
def ready_switch(rig, monkeypatch):
    server, sup, screen, calls = rig
    native = next(Path.home().glob(".codex/sessions/**/*c2a8.jsonl"))
    with native.open("a") as f:
        f.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": "Keep glacier originals",
                    },
                }
            )
            + "\n"
        )

    async def provider(harness, model, prompt, schema=None):
        if prompt.startswith("Review the proposed"):
            return '{"ready":true,"reason_codes":[]}'
        data = json.loads(prompt[prompt.index('{"prior_context"') :])
        source = next(
            (p for p in data["historical_data"] if "Keep glacier" in p["text"]),
            data["historical_data"][0],
        )
        return json.dumps(
            {
                "overview": "Retain glacier originals.",
                **{k: [] for k in memory_summary.FIELDS},
                "constraints": [
                    {
                        "text": "Keep glacier originals",
                        "refs": [source["source_id"] + ":" + source["record_id"]],
                    }
                ],
            }
        )

    monkeypatch.setattr("duckterm.memory_provider.generate", provider)
    return server, sup, screen, calls, native


async def prep(server):
    body = {
        "request_key": "prepare",
        "binding": {
            "session_key": "a",
            "source_generation": generation(server, "a"),
            "target": {"harness": "claude-code", "model": {"mode": "default"}},
        },
    }
    view = await server.memory_preparation.start("a", body)
    await server.memory_preparation.tasks[view["preparation_id"]]
    view = server.memory_preparation.status("a", view["preparation_id"])
    assert view["state"] == "ready", view
    return {
        "version": 1,
        "preparation_id": view["preparation_id"],
        "snapshot_id": view["proof"]["snapshot_id"],
        "source_generation": view["binding"]["source_generation"],
    }


def test_owner_old_switch_request_cannot_bypass_preparation(ready_switch):
    server, _, _, calls, _ = ready_switch
    status, body = dispatch(
        server,
        "POST",
        "/sessions/a/restart",
        {"x-duckterm-token": server.token},
        b'{"harness":"claude-code","interrupt":true}',
    )
    assert status == 409 and body["code"] == "preparation_expired"
    assert calls == []


def test_switch_uses_prepared_brief_and_lost_response_retry_is_durable(ready_switch, monkeypatch):
    server, _, _, calls, _ = ready_switch
    launch = server.orchestrator.launch
    seeds = []

    async def capture_launch(**kwargs):
        seeds.append(kwargs["prompt"])
        return await launch(**kwargs)

    monkeypatch.setattr(server.orchestrator, "launch", capture_launch)

    async def run():
        proof = await prep(server)
        op = await server.restarts.request(
            "a",
            "",
            "claude-code",
            interrupt=True,
            memory=proof,
            request_key="switch-1",
            require_preparation=True,
        )
        server.memory_preparation.cancel("a", proof["preparation_id"], "prepare")
        await drain(server)
        receipt = server.restarts.receipt("a", "switch-1")
        assert receipt["status"] == "completed", receipt
        assert receipt["process_state"] == "target_running"
        assert receipt["id"] == op["id"]
        assert "duckterm memory search" in seeds[0] and "Keep glacier originals" in seeds[0]
        repeat = await server.restarts.request(
            "a",
            "",
            "claude-code",
            interrupt=True,
            memory=proof,
            request_key="switch-1",
            require_preparation=True,
        )
        assert repeat["id"] == op["id"] and len(calls) == 2
        with pytest.raises(APIError, match="another switch"):
            await server.restarts.request(
                "a",
                "new",
                "claude-code",
                interrupt=True,
                memory=proof,
                request_key="switch-1",
                require_preparation=True,
            )

    asyncio.run(run())


@pytest.mark.parametrize("change", ["native", "notes", "draft", "target"])
def test_stale_proof_cannot_stop_agent(ready_switch, change):
    server, _, screen, calls, native = ready_switch

    async def run():
        proof = await prep(server)
        if change == "native":
            with native.open("a") as f:
                f.write('{"type":"new"}\n')
        elif change == "notes":
            server.history.set_meta("a", notes="New constraint")
        elif change == "draft":
            screen[0] = "────────────────\n› Unsent owner text\n────────────────"
        with pytest.raises(APIError):
            await server.restarts.request(
                "a",
                "new" if change == "target" else "",
                "claude-code",
                interrupt=True,
                memory=proof,
                request_key="switch",
                require_preparation=True,
            )
        assert calls == []

    asyncio.run(run())


def test_queued_switch_does_not_silently_extend_confirmation(ready_switch):
    server, _, _, calls, _ = ready_switch

    async def run():
        proof = await prep(server)
        await server.restarts.request(
            "a", "", "claude-code", memory=proof, request_key="queued", require_preparation=True
        )
        await hook(server)
        await drain(server)
        result = server.restarts.receipt("a", "queued")
        assert result["status"] == "failed", result
        assert result["code"] == "stale_source" and calls == []

    asyncio.run(run())


def test_owner_preparation_routes_require_owner_and_cancel_exact_lease(ready_switch):
    server, _, _, _, _ = ready_switch

    async def run():
        proof = await prep(server)
        path = "/sessions/a/restart-preparation/" + proof["preparation_id"]
        for headers in ({}, {"authorization": "Bearer session-token"}):
            writer = Writer()
            await server._dispatch("GET", path, asyncio.StreamReader(), writer, headers, b"")
            assert b"200 OK" not in writer.data
        writer = Writer()
        await server._dispatch(
            "GET",
            path + "?detail=full",
            asyncio.StreamReader(),
            writer,
            {"x-duckterm-token": server.token},
            b"",
        )
        assert b"200 OK" in writer.data and b"Keep glacier originals" in writer.data
        with pytest.raises(APIError):
            server.restarts.cancel("a", "other-operation")

    asyncio.run(run())


def test_launch_failure_reports_stopped_source_and_keeps_native_recovery(ready_switch, monkeypatch):
    server, _, _, calls, _ = ready_switch

    async def fail(**kwargs):
        raise RuntimeError("target launch failed")

    monkeypatch.setattr(server.orchestrator, "launch", fail)

    async def run():
        proof = await prep(server)
        original = server.history.session_id_for("a")
        await server.restarts.request(
            "a",
            "",
            "claude-code",
            interrupt=True,
            memory=proof,
            request_key="failed-launch",
            require_preparation=True,
        )
        await drain(server)
        receipt = server.restarts.receipt("a", "failed-launch")
        assert receipt["status"] == "failed" and receipt["process_state"] == "source_stopped"
        assert server.history.session("a")["runtime"] == "codex"
        assert server.history.session_id_for("a") == original
        assert len(calls) == 1 and calls[0][0] == "stop"

    asyncio.run(run())


def test_duplicate_request_rechecks_completed_receipt_after_slow_draft_probe(
    ready_switch, monkeypatch
):
    server, _, _, calls, _ = ready_switch

    async def run():
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        count = 0

        async def draft(*args):
            nonlocal count
            index = count
            count += 1
            entered[index].set()
            await release[index].wait()
            return True

        monkeypatch.setattr(server.restarts, "draft_free", draft)
        first = asyncio.create_task(server.restarts.request("a", "model", request_key="retry"))
        await asyncio.wait_for(entered[0].wait(), 2)
        second = asyncio.create_task(server.restarts.request("a", "model", request_key="retry"))
        await asyncio.wait_for(entered[1].wait(), 2)
        # No real process step is scheduled: this rig has no completed turn.
        # Simulate receipt completion while the duplicate is still inspecting
        # the terminal, as can happen with same-conversation model restarts.
        release[0].set()

        # describe() also probes the draft on a normal restart; avoid that
        # extra await so this test controls only request's confirmation probe.
        async def describe(key):
            return server.restarts.operation(server.restarts.read(key))

        monkeypatch.setattr(server.restarts, "describe", describe)
        original = await first
        server.restarts.save("a", status="completed", process_state="target_running")
        release[1].set()
        repeated = await second
        assert repeated["id"] == original["id"] and repeated["status"] == "completed"
        assert calls == []

    asyncio.run(run())


def test_status_observation_at_final_switch_check_does_not_stop_handoff(ready_switch, monkeypatch):
    server, _, _, calls, _ = ready_switch
    manager = server.memory_preparation
    original = manager.prepared
    checks = []

    async def observed(*args, **kwargs):
        checks.append(len(checks))
        server.history.record(
            {
                "_id": "observed-" + str(len(checks)),
                "_ts": 100,
                "session_key": "a",
                "event_type": "Notification",
                "notification_type": "idle_prompt",
                "reconciled": True,
                "test": True,
            }
        )
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager, "prepared", observed)

    async def run():
        proof = await prep(server)
        await server.restarts.request(
            "a",
            "",
            "claude-code",
            interrupt=True,
            memory=proof,
            request_key="observed-switch",
            require_preparation=True,
        )
        await drain(server)
        receipt = server.restarts.receipt("a", "observed-switch")
        assert receipt["status"] == "completed", receipt
        assert receipt["process_state"] == "target_running"
        assert len(checks) == 1  # Revalidation immediately before stopping the source.
        assert [call[0] for call in calls] == ["stop", "launch"]

    asyncio.run(run())
