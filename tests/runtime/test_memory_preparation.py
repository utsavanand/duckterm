"""Preparation never needs the source provider and never controls its process."""

import asyncio
import json

import pytest
from test_memory import transcript
from tests.runtime import test_memory as memory_fixtures

from duckterm import memory_summary
from duckterm.core.session_api import APIError
from duckterm.memory_preparation import MemoryPreparation, generation

memory_rig = memory_fixtures.memory_rig


@pytest.fixture
def preparation(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner: retain glacier originals. Later work completed.")
    calls = []

    async def live(*args):
        return server.history.session("agent"), None, None, "codex"

    async def version(*args):
        return "codex 0.155.1"

    async def provider(harness, model, prompt):
        calls.append((harness, model, prompt))
        assert harness == "codex"
        if prompt.startswith("Review the proposed"):
            return '{"ready":true,"reason_codes":[]}'
        data = json.loads(prompt[prompt.index('{"prior_context"') :])
        value = data["prior_context"] or {
            "overview": "Continue glacier work.",
            **{k: [] for k in memory_summary.FIELDS},
        }
        for piece in data["historical_data"]:
            if "Owner: retain glacier originals" in piece["text"]:
                value["constraints"] = [
                    {
                        "text": "Retain glacier originals.",
                        "refs": [piece["source_id"] + ":" + piece["record_id"]],
                    }
                ]
        return json.dumps(value)

    monkeypatch.setattr(server.restarts, "live_plan", live)
    monkeypatch.setattr(server.restarts, "switch_version", version)
    monkeypatch.setattr("duckterm.memory_provider.generate", provider)
    return server, calls, tmp


def request(server, identity="dialog-1"):
    return {
        "request_key": identity,
        "binding": {
            "session_key": "agent",
            "source_generation": generation(server, "agent"),
            "target": {"harness": "codex", "model": {"mode": "explicit", "id": "selected-model"}},
        },
    }


async def prepare(server, identity="dialog-1"):
    manager = server.memory_preparation
    view = await manager.start("agent", request(server, identity))
    task = manager.tasks.get(view["preparation_id"])
    if task:
        await task
    return manager.status("agent", view["preparation_id"])


def test_preparation_saves_one_canonical_revision_and_marker_without_source_provider(preparation):
    server, calls, _ = preparation
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready", view
    assert view["coverage"]["available_text"] == "processed"
    detail = server.memory_preparation.details("agent", view["preparation_id"])
    assert "Retain glacier originals" in detail["brief"]["text"]
    assert "duckterm memory search" in detail["brief"]["text"]
    row = server.history.session("agent")
    revision_id = json.loads(row["progress"])["revision_id"]
    assert revision_id == view["proof"]["revision_id"]
    cp = server.history.checkpoints("agent")[0]
    assert cp["record"]["summary_ref"] == revision_id
    assert cp["record"]["memory_sources"]
    assert {c[0] for c in calls} == {"codex"}
    assert server.history.session("agent")["runtime"] == "claude-code"


def test_equivalent_dialogs_share_generation_but_have_independent_cancel_leases(preparation):
    server, calls, _ = preparation

    async def run():
        a = await server.memory_preparation.start("agent", request(server, "a"))
        b = await server.memory_preparation.start("agent", request(server, "b"))
        assert a["preparation_id"] == b["preparation_id"]
        assert server.memory_preparation.cancel("agent", a["preparation_id"], "a") == {
            "released": True
        }
        await server.memory_preparation.tasks[a["preparation_id"]]
        assert server.memory_preparation.status("agent", a["preparation_id"])["state"] == "ready"
        count = len(calls)
        replay = await server.memory_preparation.start("agent", request(server, "b"))
        assert replay["preparation_id"] == b["preparation_id"] and len(calls) == count
        body = request(server, "b")
        body["binding"]["target"]["model"]["id"] = "changed"
        with pytest.raises(APIError, match="another target"):
            await server.memory_preparation.start("agent", body)

    asyncio.run(run())


def test_changed_source_and_provider_failure_never_promote_ready_revision(preparation, monkeypatch):
    server, _, tmp = preparation
    original = __import__("duckterm.memory_provider", fromlist=["generate"]).generate

    async def changed(*args):
        result = await original(*args)
        server.history.set_meta("agent", notes="New owner constraint")
        return result

    monkeypatch.setattr("duckterm.memory_provider.generate", changed)
    view = asyncio.run(prepare(server))
    assert view["state"] == "stale_source", view
    assert not server.history.checkpoints("agent")
    assert server.history.session("agent")["runtime"] == "claude-code"

    async def failed(*args):
        raise APIError(503, "Quota exhausted")

    monkeypatch.setattr("duckterm.memory_provider.generate", failed)
    view = asyncio.run(prepare(server, "retry"))
    assert view["state"] == "failed" and view["retryable"]
    assert not server.history.checkpoints("agent")


def test_ready_proof_becomes_stale_after_source_or_scope_change(preparation):
    server, _, tmp = preparation
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready", view
    transcript(tmp, "agent-claude", "Owner changed the glacier constraint")
    assert (
        server.memory_preparation.status("agent", view["preparation_id"])["state"] == "stale_source"
    )
    with pytest.raises(APIError):
        server.memory_preparation.details("peer", view["preparation_id"])


def test_unchanged_prefix_reuses_summary_but_early_rewrite_forces_complete_read(monkeypatch):
    calls = []

    async def provider(harness, model, prompt):
        calls.append(prompt)
        if prompt.startswith("Review the proposed"):
            return '{"ready":true,"reason_codes":[]}'
        return json.dumps({"overview": "Summary", **{k: [] for k in memory_summary.FIELDS}})

    monkeypatch.setattr("duckterm.memory_provider.generate", provider)
    target = {"harness": "codex", "model": {"mode": "default"}}
    sources = [
        {
            "id": "native",
            "kind": "conversation",
            "records": [{"id": "0", "role": "user", "text": "early-owner-constraint"}],
        }
    ]
    context, hashes = asyncio.run(memory_summary.summarize(sources, target))
    prior = {
        "summary_validation": {"ready": True},
        "memory": {
            "context": context,
            "pieces": hashes,
            "policy": memory_summary.POLICY,
            "target": target,
        },
    }
    calls.clear()
    sources[0]["records"].append({"id": "1", "role": "assistant", "text": "new-result"})
    asyncio.run(memory_summary.summarize(sources, target, prior))
    assert len(calls) == 2 and "new-result" in calls[0] and "early-owner-constraint" not in calls[0]
    sources[0]["records"][0]["text"] = "rewritten-owner-constraint"
    calls.clear()
    asyncio.run(memory_summary.summarize(sources, target, prior))
    assert "rewritten-owner-constraint" in calls[0] and "new-result" in calls[0]


def test_reconstruction_requires_new_preparation_and_preserves_canonical_revision(preparation):
    server, _, _ = preparation
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready", view
    server.memory_preparation = MemoryPreparation(server)
    with pytest.raises(APIError, match="expired"):
        server.memory_preparation.get("agent", view["preparation_id"])
    assert server.history.checkpoints("agent")[0]["summary"] == "Continue glacier work."


def test_source_failure_does_not_call_any_provider(preparation):
    server, calls, tmp = preparation
    native = transcript(tmp, "agent-claude", "glacier")
    native.unlink()
    view = asyncio.run(prepare(server))
    assert view["state"] == "incomplete_source", view
    assert calls == []


def test_turn_progress_updates_overview_without_extending_whole_history_coverage(
    preparation, monkeypatch
):
    server, _, tmp = preparation

    def summarize(prompt):
        if "validating a candidate" in prompt:
            value = {
                "accept": [],
                "done_next_action_ids": [],
                "summary_validation": {"ready": True, "reason_codes": []},
            }
        else:
            value = {"summary": "New work after the prepared handoff.", "deliverables": []}
        return type("Summary", (), {"text": json.dumps(value)})()

    monkeypatch.setattr("duckterm.server.summarize", summarize)

    async def run():
        view = await prepare(server)
        assert view["state"] == "ready", view
        baseline_id = view["proof"]["revision_id"]
        native = tmp / ".claude" / "projects"
        path = next(native.rglob("agent-claude.jsonl"))
        with path.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"role": "assistant", "content": "New completed work"},
                    }
                )
                + "\n"
            )
        value = await server.progress_coordinator.refresh("agent")
        assert value["memory_baseline_ref"] == baseline_id
        assert not value["summary_validation"]["ready"]
        assert json.loads(server.history.session("agent")["progress"])["revision_id"] == value["id"]
        assert server.digests.revision("agent", baseline_id)["memory"]["context"]["constraints"]

    asyncio.run(run())


def test_status_never_returns_brief_or_keeps_proof_after_source_invalidation(preparation):
    server, _, _ = preparation
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready"
    assert "brief" not in view["proof"]
    assert server.memory_preparation.details("agent", view["preparation_id"])["brief"]["text"]
    server.history.set_meta("agent", notes="Owner changed the continuation constraint")
    stale = server.memory_preparation.status("agent", view["preparation_id"])
    assert stale["state"] == "stale_source"
    assert "proof" not in stale
    assert "Retain glacier originals" not in json.dumps(stale)


@pytest.mark.parametrize(
    "event",
    [
        {"event_type": "Attended", "reconciled": True},
        {"event_type": "Notification", "reconciled": True, "notification_type": "idle_prompt"},
        {
            "event_type": "Notification",
            "launched": True,
            "runtime": "claude-code",
            "branch": "main",
        },
    ],
)
def test_observing_idle_session_during_and_after_preparation_keeps_proof(
    preparation, monkeypatch, event
):
    server, calls, _ = preparation
    from duckterm import memory_provider

    original = memory_provider.generate

    def observe(identity):
        server.history.record(
            {
                "_id": identity,
                "_ts": 100 + len(calls),
                "session_key": "agent",
                "test": True,
                **event,
            }
        )

    async def observed(*args):
        result = await original(*args)
        observe("observation-" + str(len(calls)))
        return result

    monkeypatch.setattr(memory_provider, "generate", observed)
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready", view
    proof = {
        "version": 1,
        "preparation_id": view["preparation_id"],
        "snapshot_id": view["proof"]["snapshot_id"],
        "source_generation": view["binding"]["source_generation"],
    }
    observe("after-ready")
    assert server.memory_preparation.status("agent", view["preparation_id"])["state"] == "ready"
    prepared = asyncio.run(
        server.memory_preparation.prepared("agent", proof, "codex", "selected-model")
    )
    assert prepared["seed"]
    cp = server.history.checkpoints("agent")[0]
    assert cp["coverage"]["state"] == "retained"
    assert (
        server.history._conn.execute(
            "SELECT count(*) FROM events WHERE id='after-ready'"
        ).fetchone()[0]
        == 1
    )
    server.history.record(
        {
            "_id": "real-owner-message",
            "_ts": 200,
            "session_key": "agent",
            "event_type": "UserPromptSubmit",
            "prompt": "New owner constraint",
            "test": True,
        }
    )
    assert (
        server.memory_preparation.status("agent", view["preparation_id"])["state"] == "stale_source"
    )


@pytest.mark.parametrize(
    "event",
    [
        {"event_type": "Notification", "message": "New information"},
        {"event_type": "Notification", "notification_type": "permission_prompt"},
        {"event_type": "Notification", "new_payload": "Unknown must remain material"},
        {"event_type": "Attended", "prompt": "Do not lose this constraint"},
        {"event_type": "Stop"},
    ],
)
def test_content_notifications_and_unknown_payloads_still_invalidate(preparation, event):
    server, _, _ = preparation
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready"
    server.history.record(
        {"_id": "new-content", "_ts": 200, "session_key": "agent", "test": True, **event}
    )
    stale = server.memory_preparation.status("agent", view["preparation_id"])
    assert stale["state"] == "stale_source"
    assert "proof" not in stale
