"""A lost checkpoint response is recoverable without rerunning generation."""

import asyncio
import json

import pytest
from test_saved_checkpoint import rig

from duckterm.checkpoint_requests import MAX_RECEIPTS, CheckpointRequests
from duckterm.core.session_api import APIError

__all__ = ["rig"]


def request(server, identity="request-one", **fields):
    return server.checkpoint_requests.request("test-save", {"request_id": identity, **fields})


def test_complete_request_replays_exact_result_after_newer_checkpoint_and_restart(rig):
    server, _, calls = rig

    async def run():
        status, first = await request(server)
        assert status == 200 and first["request_id"] == "request-one"
        assert first["record"]["request_id"] == "request-one"
        await request(server, "request-two")
        before = len(calls)
        server.checkpoint_requests = CheckpointRequests(server)
        status, replay = await request(server)
        assert status == 200 and replay["id"] == first["id"]
        assert len(calls) == before
        assert len(server.history.checkpoints("test-save")) == 2
        assert (
            server.checkpoint_requests.receipt("test-save", "request-one")["result"]["id"]
            == first["id"]
        )

    asyncio.run(run())


def test_cancelled_http_waiter_and_duplicate_share_one_surviving_operation(rig, monkeypatch):
    server, _, _ = rig
    original = server._create_checkpoint

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def create(*args, **kwargs):
            nonlocal count
            count += 1
            started.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(server, "_create_checkpoint", create)
        first = asyncio.create_task(request(server))
        await started.wait()
        assert server.checkpoint_requests.receipt("test-save", "request-one")["state"] == "running"
        with pytest.raises(APIError, match="already running"):
            await request(server, "other-request")
        duplicate = asyncio.create_task(request(server))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        status, result = await duplicate
        assert status == 200 and result["saved"] and count == 1
        assert len(server.history.checkpoints("test-save")) == 1

    asyncio.run(run())


def test_summary_rejection_is_complete_request_but_failed_summary(rig, monkeypatch):
    from duckterm.llm.summarizer import Summary

    server, _, _ = rig
    monkeypatch.setattr("duckterm.server.summarize", lambda _: Summary("", "none"))
    status, result = asyncio.run(request(server))
    assert status == 200 and result["summary_update"]["state"] == "failed"
    receipt = server.checkpoint_requests.receipt("test-save", "request-one")
    assert receipt["state"] == "complete"
    assert not receipt["result"]["handoff_eligible"]


def test_error_is_durable_fixed_code_and_never_runs_again(rig, monkeypatch):
    server, _, _ = rig
    calls = []

    async def broken(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("private provider content")

    monkeypatch.setattr(server, "_create_checkpoint", broken)
    status, first = asyncio.run(request(server))
    assert status == 500 and first["state"] == "failed"
    assert first["error"] == "checkpoint_failed"
    server.checkpoint_requests = CheckpointRequests(server)
    assert asyncio.run(request(server)) == (status, first)
    assert len(calls) == 1
    assert "private provider" not in json.dumps(server.history.restart_control("test-save"))


def test_committed_checkpoint_wins_over_lost_receipt_or_export_error(rig, monkeypatch):
    server, _, _ = rig

    async def broken(*args):
        raise OSError("export failed after commit")

    monkeypatch.setattr(server, "_export_checkpoint", broken)
    status, result = asyncio.run(request(server))
    assert status == 200 and result["saved"]
    receipt = server.history.restart_control("test-save")["checkpoint_receipts"]["request-one"]
    receipt["state"] = "running"
    server.checkpoint_requests._save("test-save", receipt)
    server.checkpoint_requests = CheckpointRequests(server)
    recovered = server.checkpoint_requests.receipt("test-save", "request-one")
    assert recovered["state"] == "complete" and recovered["result"]["id"] == result["id"]


def test_restart_without_checkpoint_is_interrupted_not_success_or_automatic_retry(rig):
    server, _, calls = rig
    receipt = {
        "request_id": "before-crash",
        "checkpoint_id": "not-saved",
        "label": "checkpoint",
        "state": "running",
    }
    server.checkpoint_requests._save("test-save", receipt)
    server.checkpoint_requests = CheckpointRequests(server)
    status, result = asyncio.run(request(server, "before-crash"))
    assert status == 409 and result["state"] == "interrupted"
    assert calls == [] and server.history.checkpoints("test-save") == []


def test_label_collision_and_invalid_id_fail_before_provider(rig):
    server, _, calls = rig

    async def run():
        await request(server, label="manual")
        before = len(calls)
        with pytest.raises(APIError, match="different label"):
            await request(server, label="other")
        for identity in (None, "../secret", "x" * 129, 1, ""):
            with pytest.raises(APIError):
                await request(server, identity)
        assert len(calls) == before

    asyncio.run(run())


def test_receipt_scope_and_deleted_session_do_not_expose_other_results(rig):
    server, _, _ = rig
    asyncio.run(request(server))
    server.bus.publish({"event_type": "SessionStart", "session_key": "other", "test": True})
    try:
        with pytest.raises(APIError) as exc:
            server.checkpoint_requests.receipt("other", "request-one")
        assert exc.value.status == 404
        server.history.delete_session("test-save")
        with pytest.raises(APIError) as exc:
            server.checkpoint_requests.receipt("test-save", "request-one")
        assert exc.value.status == 404
    finally:
        server.history.delete_session("other")


def test_receipts_are_bounded_and_do_not_overwrite_restart_fields(rig):
    server, _, _ = rig
    server.history.set_restart_control("test-save", {"native_binding": {"generation": 3}})
    for index in range(MAX_RECEIPTS + 1):
        server.checkpoint_requests._save("test-save", {"request_id": str(index), "state": "failed"})
    control = server.history.restart_control("test-save")
    assert control["native_binding"] == {"generation": 3}
    assert len(control["checkpoint_receipts"]) == MAX_RECEIPTS
    assert "0" not in control["checkpoint_receipts"]


def test_http_contract_requires_write_token_and_recovers_exact_id(rig):
    from test_session_api import dispatch

    server, _, _ = rig
    path = "/sessions/test-save/checkpoint"
    body = json.dumps({"request_id": "http-request", "label": "manual"}).encode()
    owner = {"x-duckterm-token": server.token}
    assert dispatch(server, "POST", path, {}, body)[0] == 401
    status, result = dispatch(server, "POST", path, owner, body)
    assert status == 200 and result["request_id"] == "http-request"
    receipt_path = "/sessions/test-save/checkpoint-attempts/http-request"
    status, receipt = dispatch(server, "GET", receipt_path, {})
    assert status == 200 and receipt["result"]["id"] == result["id"]
    assert dispatch(server, "GET", receipt_path, {"authorization": "Bearer agent"})[0] == 403
    from test_session_api import Writer

    writer = Writer()

    async def cross_origin():
        await server._dispatch(
            "GET",
            receipt_path,
            asyncio.StreamReader(),
            writer,
            {"origin": "https://evil.example"},
            b"",
        )

    asyncio.run(cross_origin())
    assert int(writer.data.split(b"\r\n", 1)[0].split()[1]) == 403
    assert dispatch(server, "GET", receipt_path + "-absent", {})[0] == 404
    for invalid in (b"null", b"[]", b"{"):
        assert dispatch(server, "POST", path, owner, invalid)[0] == 400


def test_shutdown_records_interruption_and_does_not_retry(rig, monkeypatch):
    server, _, _ = rig

    async def run():
        started = asyncio.Event()

        async def hang(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(server, "_create_checkpoint", hang)
        waiter = asyncio.create_task(request(server))
        await started.wait()
        await server.checkpoint_requests.close()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert (
            server.checkpoint_requests.receipt("test-save", "request-one")["state"] == "interrupted"
        )
        assert server.history.checkpoints("test-save") == []

    asyncio.run(run())


def test_orphaned_running_receipts_cannot_fill_capacity_forever(rig, monkeypatch):
    server, _, _ = rig
    for index in range(MAX_RECEIPTS):
        server.checkpoint_requests._save(
            "test-save",
            {
                "request_id": str(index),
                "state": "running",
                "checkpoint_id": "absent",
                "label": "checkpoint",
            },
        )
    status, result = asyncio.run(request(server))
    assert status == 200 and result["saved"]
    assert len(server.checkpoint_requests._receipts("test-save")) == MAX_RECEIPTS
