"""Slow preparation retains reviewed work without admitting partial coverage."""

import asyncio
import json

import pytest
from test_memory import transcript
from test_memory_preparation import prepare, request
from tests.runtime import test_memory_preparation as fixtures

from duckterm import memory_provider, memory_summary
from duckterm.core.session_api import APIError

preparation = fixtures.preparation
memory_rig = fixtures.memory_rig


def summary():
    return {"overview": "Continue the task.", **{k: [] for k in memory_summary.FIELDS}}


def data(prompt):
    return json.loads(prompt[prompt.index('{"prior_context"') :])


def test_timeout_splits_utf8_text_in_order_and_counts_only_whole_reviewed_batch(monkeypatch):
    source = {
        "id": "s",
        "kind": "conversation",
        "records": [{"id": "r", "role": "user", "text": "é🙂" * 8001}],
    }
    drafts, accepted, progress = [], [], []
    calls = []

    async def provider(harness, model, prompt, schema):
        calls.append(schema)
        if schema == memory_summary.VERDICT_SCHEMA:
            assert not accepted
            return '{"ready":true,"reason_codes":[]}'
        value = data(prompt)
        drafts.append(value)
        if len(drafts) == 1:
            raise memory_provider.BatchTimeout()
        return json.dumps(summary())

    monkeypatch.setattr(memory_provider, "generate", provider)
    context, hashes = asyncio.run(
        memory_summary.summarize(
            [source],
            {"harness": "codex", "model": {"mode": "default"}},
            retain=accepted.append,
            progress=lambda *p: progress.append(p),
        )
    )
    left, right = (drafts[i]["historical_data"][0] for i in (1, 2))
    assert left["text"] + right["text"] == source["records"][0]["text"]
    assert left["offset"] == 0 and right["offset"] == len(left["text"].encode())
    assert left["record_id"] == right["record_id"] == "r"
    assert drafts[2]["prior_context"] == context == summary()
    assert hashes == {"s": [memory_summary.pieces([source])[0]["hash"]]}
    assert len(calls) == 5 and len(accepted) == 1
    assert accepted[0].pieces == hashes and accepted[0].completed == 1
    assert [p for p in progress if p[-1] == "complete"] == [(1, 1, "complete")]


@pytest.mark.parametrize("timeout", [True, False])
def test_adaptive_retry_is_bounded_and_never_retries_quota_errors(monkeypatch, timeout):
    calls, accepted = [], []

    async def provider(*args):
        calls.append(args)
        raise memory_provider.BatchTimeout() if timeout else APIError(503, "Quota exhausted")

    monkeypatch.setattr(memory_provider, "generate", provider)
    with pytest.raises(APIError):
        asyncio.run(
            memory_summary.summarize(
                [
                    {
                        "id": "s",
                        "kind": "conversation",
                        "records": [{"id": "r", "role": "user", "text": "x" * 96000}],
                    }
                ],
                {"harness": "codex", "model": {"mode": "default"}},
                retain=accepted.append,
            )
        )
    assert len(calls) == (3 if timeout else 1)
    assert accepted == []


@pytest.mark.parametrize(
    "change",
    ["none", "notes", "source", "model", "cli", "policy", "expired", "canceled", "restart"],
)
def test_failed_capture_retries_with_fresh_fences_without_provider(
    preparation, monkeypatch, change
):
    from duckterm import memory_versions
    from duckterm.core import saved_progress

    server, calls, tmp = preparation
    original = memory_versions.retain
    fail = True

    def retaining(*args):
        if fail:
            raise APIError(503, "Temporary storage failure")
        return original(*args)

    monkeypatch.setattr(memory_versions, "retain", retaining)

    async def run():
        nonlocal fail
        manager = server.memory_preparation
        first = await prepare(server)
        assert first["state"] == "failed" and first["retryable"]
        assert not server.history.checkpoints("agent")
        assert "proof" not in first and calls == []
        assert not server.history.session("agent")["progress"]
        job = manager.jobs[first["preparation_id"]]
        if change == "canceled":
            manager.cancel("agent", first["preparation_id"], "dialog-1")
        elif change == "notes":
            server.history.set_meta("agent", notes="New owner constraint")
        elif change == "source":
            transcript(tmp, "agent-claude", "Changed owner instructions.")
        elif change == "cli":
            job["cli_version"] = "old CLI"
        elif change == "policy":
            monkeypatch.setattr(saved_progress, "POLICY", "new-test-policy")
        elif change == "expired":
            job["expires"] = 0
        elif change == "restart":
            server.memory_preparation = type(manager)(server)
            manager = server.memory_preparation
        fail = False
        body = request(server, "retry")
        if change == "model":
            body["binding"]["target"]["model"]["id"] = "different-model"
        started = await manager.start("agent", body)
        await manager.tasks[started["preparation_id"]]
        result = manager.status("agent", started["preparation_id"])
        assert result["state"] == "ready", result
        assert result["preparation_id"] != first["preparation_id"]
        assert len(server.history.checkpoints("agent")) == 1
        assert server.history.session("agent")["runtime"] == "claude-code"
        assert calls == []
        brief = manager.details("agent", result["preparation_id"])["brief"]["text"]
        if change == "source":
            assert "Changed owner instructions" in brief
        if change == "notes":
            assert "New owner constraint" in brief

    asyncio.run(run())
