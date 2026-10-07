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
def test_failed_job_reuses_only_reviewed_progress_under_matching_fences(
    preparation, monkeypatch, change
):
    server, calls, tmp = preparation
    original = memory_provider.generate
    monkeypatch.setattr(memory_summary, "bundle", lambda pieces: [[p] for p in pieces])
    generation_calls = []
    fail = True

    async def provider(harness, model, prompt, schema):
        if schema == memory_summary.SUMMARY_SCHEMA:
            generation_calls.append(data(prompt)["historical_data"])
            if fail and len(generation_calls) == 2:
                raise APIError(503, "Temporary provider failure")
        return await original(harness, model, prompt, schema)

    monkeypatch.setattr(memory_provider, "generate", provider)

    async def run():
        nonlocal fail
        manager = server.memory_preparation
        if change == "canceled":
            retain = manager.retain_progress

            def cancel_after_review(job, value):
                retain(job, value)
                manager.cancel("agent", job["id"], "dialog-1")

            monkeypatch.setattr(manager, "retain_progress", cancel_after_review)
        first = await prepare(server)
        assert first["state"] == ("canceled" if change == "canceled" else "failed")
        assert not server.history.checkpoints("agent")
        assert "partial" not in first and "proof" not in first
        assert not server.history.session("agent")["progress"]
        job = manager.jobs[first["preparation_id"]]
        initial = generation_calls[0]
        if change == "canceled":
            assert "partial" not in job
            monkeypatch.setattr(manager, "retain_progress", retain)
        else:
            assert job["partial"].completed == 1
            # Closing a failed dialog must not erase work for its retry.
            manager.cancel("agent", first["preparation_id"], "dialog-1")
        if change == "notes":
            server.history.set_meta("agent", notes="New owner constraint")
        elif change == "source":
            transcript(tmp, "agent-claude", "Changed owner instructions.")
        elif change == "cli":
            job["cli_version"] = "old CLI"
        elif change == "policy":
            monkeypatch.setattr(memory_summary, "POLICY", "new-test-policy")
        elif change == "expired":
            job["expires"] = 0
        elif change == "restart":
            server.memory_preparation = type(manager)(server)
            manager = server.memory_preparation
        fail = False
        generation_calls.clear()
        body = request(server, "retry")
        if change == "model":
            body["binding"]["target"]["model"]["id"] = "different-model"
        started = await manager.start("agent", body)
        await manager.tasks[started["preparation_id"]]
        result = manager.status("agent", started["preparation_id"])
        assert result["state"] == "ready", result
        assert len(server.history.checkpoints("agent")) == 1
        assert server.history.session("agent")["runtime"] == "claude-code"
        assert "partial" not in manager.jobs[started["preparation_id"]]
        if change == "none":
            assert initial not in generation_calls
            assert result["progress"]["completed_batches"] == len(generation_calls) + 1
        elif change != "source":
            assert generation_calls[0] == initial
        else:
            assert any("Changed owner" in p["text"] for g in generation_calls for p in g)

    asyncio.run(run())
