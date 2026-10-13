"""Explicit pending-record selection reuses checkpoint publication and receipts."""

import asyncio
import json

import pytest
from test_checkpoint_window import model_provider, writer_texts
from test_maintained_progress import append
from test_memory import memory_rig, transcript

from duckterm import checkpoint_plan, checkpoint_settings
from duckterm.core.session_api import APIError

__all__ = ["memory_rig"]


def history(tmp, count):
    path = transcript(tmp, "agent-claude", "Record 0")
    for n in range(1, count):
        append(path, f"Record {n}")
    return path


async def submit(server, preview, count=20, request="test-plan"):
    return await server.checkpoint_requests.request(
        "agent",
        {
            "request_id": request,
            "selection": {"plan": preview["selection_plan"], "records": count},
        },
    )


def test_preview_is_read_only_and_selection_advances_over_actual_coverage(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    history(tmp, 55)
    calls, _ = model_provider(monkeypatch)

    async def run():
        first = await checkpoint_plan.preview(server, "agent")
        assert first["pending_records"] == 55
        assert first["selected_records"] == 20 and first["remaining_records"] == 35
        assert not calls and not server.history.checkpoints("agent")
        status, saved = await submit(server, first)
        assert status == 200 and saved["summary_update"]["state"] == "updated", saved
        assert writer_texts(calls) == [[f"Record {i}" for i in range(20)]]
        next_plan = await checkpoint_plan.preview(server, "agent")
        assert next_plan["processed_records"] == 20
        assert next_plan["pending_records"] == 35
        status, saved = await submit(server, next_plan, "all", "test-all")
        assert status == 200 and saved["summary_update"]["state"] == "updated", saved
        assert [len(v) for v in writer_texts(calls)] == [20, 20, 15]
        final = await checkpoint_plan.preview(server, "agent")
        assert final["pending_records"] == final["selected_records"] == 0
        assert checkpoint_settings.load()["lookback_records"] == 20

    asyncio.run(run())


def test_new_messages_do_not_change_the_confirmed_selection(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = history(tmp, 3)
    calls, _ = model_provider(monkeypatch)

    async def run():
        plan = await checkpoint_plan.preview(server, "agent")
        append(path, "New message after preview")
        status, saved = await submit(server, plan, "all")
        assert status == 200 and saved["summary_update"]["state"] == "updated", saved
        assert writer_texts(calls) == [["Record 0", "Record 1", "Record 2"]]
        remaining = await checkpoint_plan.preview(server, "agent")
        assert remaining["pending_records"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["edit", "conversation", "settings", "scope", "coverage"])
def test_stale_plan_cannot_start_generation_or_save_checkpoint(memory_rig, monkeypatch, change):
    server, _, _, tmp = memory_rig
    path = history(tmp, 3)
    calls, _ = model_provider(monkeypatch)

    async def run():
        plan = await checkpoint_plan.preview(server, "agent")
        if change == "edit":
            path.write_text(path.read_text().replace("Record 0", "Edited 0"))
        elif change == "conversation":
            plan["selection_plan"]["conversation"] = "different"
        elif change == "settings":
            plan["selection_plan"]["settings_hash"] = "different"
        elif change == "scope":
            plan["selection_plan"]["boundary"]["scope"] = "different"
        else:
            await submit(server, plan, 1, "first-checkpoint")
        before = len(calls)
        count = len(server.history.checkpoints("agent"))
        status, result = await submit(server, plan)
        assert status == 409 and result["error"] == "checkpoint_source_changed", result
        assert len(calls) == before
        assert len(server.history.checkpoints("agent")) == count

    asyncio.run(run())


def test_receipt_replays_same_selection_but_rejects_changed_count(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    history(tmp, 3)
    calls, _ = model_provider(monkeypatch)

    async def run():
        plan = await checkpoint_plan.preview(server, "agent")
        _, first = await submit(server, plan, 1)
        count = len(calls)
        _, replay = await submit(server, plan, 1)
        assert first["id"] == replay["id"] and len(calls) == count
        with pytest.raises(APIError, match="different checkpoint options"):
            await submit(server, plan, 2)

    asyncio.run(run())


@pytest.mark.parametrize("count", [0, -1, 1001, True, None, [], "20"])
def test_invalid_count_rejected_before_receipt(memory_rig, count):
    server, _, _, _ = memory_rig
    with pytest.raises(APIError, match="Choose"):
        asyncio.run(submit(server, {"selection_plan": {}}, count))
    assert not server.checkpoint_requests._receipts("agent")


def test_http_plan_is_read_only_and_submit_keeps_write_auth(memory_rig, monkeypatch):
    from test_session_api import Writer, dispatch

    server, headers, _, tmp = memory_rig
    history(tmp, 3)
    calls, _ = model_provider(monkeypatch)
    path = "/sessions/agent/checkpoint-plan"
    status, plan = dispatch(server, "GET", path, {})
    assert status == 200 and plan["pending_records"] == 3
    assert not calls and not server.checkpoint_requests._receipts("agent")
    assert dispatch(server, "GET", path, headers)[0] == 403
    writer = Writer()

    async def cross_origin():
        await server._dispatch(
            "GET", path, asyncio.StreamReader(), writer, {"origin": "https://example.com"}, b""
        )

    asyncio.run(cross_origin())
    assert int(writer.data.split(b"\r\n", 1)[0].split()[1]) == 403
    assert dispatch(server, "GET", "/sessions/absent/checkpoint-plan", {})[0] == 404
    payload = json.dumps(
        {"request_id": "http-plan", "selection": {"plan": plan["selection_plan"], "records": 2}}
    ).encode()
    assert dispatch(server, "POST", "/sessions/agent/checkpoint", {}, payload)[0] == 401
    owner = {"x-duckterm-token": server.token}
    status, result = dispatch(server, "POST", "/sessions/agent/checkpoint", owner, payload)
    assert status == 200 and result["summary_update"]["state"] == "updated", result
    assert writer_texts(calls) == [["Record 0", "Record 1"]]


def test_previous_recent_window_does_not_hide_older_pending_records(memory_rig, monkeypatch):
    from test_checkpoint_window import checkpoint

    server, _, _, tmp = memory_rig
    history(tmp, 25)
    calls, _ = model_provider(monkeypatch)

    async def run():
        await checkpoint(server)
        plan = await checkpoint_plan.preview(server, "agent")
        assert plan["pending_records"] == plan["selected_records"] == 5
        assert plan["processed_records"] == 20
        _, result = await submit(server, plan)
        assert result["summary_update"]["state"] == "updated", result
        assert writer_texts(calls)[-1] == [f"Record {i}" for i in range(5)]
        assert (await checkpoint_plan.preview(server, "agent"))["pending_records"] == 0

    asyncio.run(run())


def test_provider_failure_leaves_unaccepted_records_pending(memory_rig, monkeypatch):
    from test_memory_spans import provider

    server, _, _, tmp = memory_rig
    history(tmp, 3)
    _, fail = provider(monkeypatch)

    async def run():
        plan = await checkpoint_plan.preview(server, "agent")
        fail[0] = True
        _, result = await submit(server, plan)
        assert result["summary_update"]["state"] == "failed", result
        retry = await checkpoint_plan.preview(server, "agent")
        assert retry["pending_records"] == 3
        fail[0] = False
        _, result = await submit(server, retry, request="retry-success")
        assert result["summary_update"]["state"] == "updated", result
        empty = await checkpoint_plan.preview(server, "agent")
        assert empty["pending_records"] == 0
        before = len(server.history.checkpoints("agent"))
        status, _ = await submit(server, empty, request="no-work")
        assert status == 409 and len(server.history.checkpoints("agent")) == before

    asyncio.run(run())


def test_unavailable_history_does_not_look_like_zero_pending(memory_rig):
    server, _, _, _ = memory_rig
    with pytest.raises(APIError, match="unavailable"):
        asyncio.run(checkpoint_plan.preview(server, "agent"))
    assert not server.history.checkpoints("agent")


@pytest.mark.parametrize("plan", [None, {}, [], {"version": 1}])
def test_malformed_plan_is_rejected_before_receipt(memory_rig, plan):
    server, _, _, _ = memory_rig
    with pytest.raises(APIError, match="Invalid checkpoint selection plan"):
        asyncio.run(submit(server, {"selection_plan": plan}))
    assert not server.checkpoint_requests._receipts("agent")
