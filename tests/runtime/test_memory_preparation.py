"""Preparation completes typed spans before proof and never controls the process."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from test_maintained_progress import append
from test_memory import transcript
from tests.runtime import test_memory as memory_fixtures

from duckterm import checkpoint_plan, memory_summary
from duckterm.core.session_api import APIError
from duckterm.memory_preparation import MemoryPreparation, generation

memory_rig = memory_fixtures.memory_rig


def typed_provider(monkeypatch, claim_text):
    from duckterm.llm.summarizer import Summary

    calls = []

    def generate(prompt, **_kwargs):
        calls.append(prompt)
        if prompt.startswith("You are validating"):
            return Summary(
                json.dumps(
                    {
                        "accept": [],
                        "done_next_action_ids": [],
                        "summary_validation": {"ready": True, "reason_codes": []},
                    }
                ),
                "fixture",
            )
        data = json.loads(prompt.split("MEMORY UPDATE:\n", 1)[1])
        assert "prior_context" not in data
        context = {
            "overview": "Continue glacier work.",
            **{field: [] for field in memory_summary.FIELDS},
        }
        evidence = [r for r in data["records"] if claim_text.lower() in r["text"].lower()]
        if evidence:
            context["constraints"] = [
                {
                    "text": claim_text,
                    "refs": [
                        r["source"] + ":" + r["version"] + ":" + r["record"] for r in evidence
                    ],
                    "kind": "constraint",
                    "state": "open",
                }
            ]
        return Summary(
            json.dumps({"summary": "Continue glacier work.", "context": context}), "fixture"
        )

    monkeypatch.setattr("duckterm.server.summarize", generate)
    return calls


@pytest.fixture
def preparation(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner: retain glacier originals. Later work completed.")
    calls = typed_provider(monkeypatch, "retain glacier originals")

    async def live(*args):
        return server.history.session("agent"), None, None, "codex"

    async def version(*args):
        return "codex 0.155.1"

    monkeypatch.setattr(server.restarts, "live_plan", live)
    monkeypatch.setattr(server.restarts, "switch_version", version)
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


def test_preparation_completes_spans_before_creating_proof(preparation):
    server, calls, _ = preparation
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready", view
    assert view["coverage"]["available_text"] == "processed"
    detail = server.memory_preparation.details("agent", view["preparation_id"])
    assert "retain glacier originals" in detail["brief"]["text"]
    assert "duckterm memory search" in detail["brief"]["text"]
    assert server.history.session("agent")["progress"]
    assert view["proof"]["revision_id"]
    cp = server.history.checkpoints("agent")[0]
    assert cp["record"]["summary_ref"] == view["proof"]["revision_id"]
    assert cp["record"]["memory_sources"] and cp["record"]["retained_sources"]
    assert len(calls) == 2
    assert server.history.session("agent")["runtime"] == "claude-code"


@pytest.mark.parametrize("count,expected_batches", [(20, 1), (35, 2), ("all", 3)])
def test_selected_recent_history_prepares_partial_handoff_without_processing_backlog(
    preparation, count, expected_batches
):
    server, calls, tmp = preparation
    path = next(tmp.rglob("agent-claude.jsonl"))
    for number in range(1, 41):
        append(path, f"Message {number}")

    async def run():
        plan = await checkpoint_plan.preview(server, "agent")
        assert plan["pending_records"] >= 41
        body = request(server)
        body["selection"] = {"plan": plan["selection_plan"], "records": count}
        view = await server.memory_preparation.start("agent", body)
        await server.memory_preparation.tasks[view["preparation_id"]]
        view = server.memory_preparation.status("agent", view["preparation_id"])
        assert view["state"] == "ready", (view.get("reason"), view.get("progress"))
        handoff = view["coverage"]["handoff"]
        expected = min(
            count if count != "all" else plan["pending_records"], plan["pending_records"]
        )
        assert handoff["summary_state"] == ("current" if count == "all" else "partial")
        assert handoff["summarized_records"] == expected
        assert handoff["eligible_records"] >= 41
        assert view["coverage"]["available_text"] == ("processed" if count == "all" else "partial")
        if count != "all":
            assert handoff["coverage"]["omitted"][0]["reason"] == "pending_update"
        detail = server.memory_preparation.details("agent", view["preparation_id"])
        assert "duckterm memory search" in detail["brief"]["text"]
        written = [
            json.loads(prompt.split("MEMORY UPDATE:\n", 1)[1])["records"]
            for prompt in calls
            if not prompt.startswith("You are validating")
        ]
        assert sum(map(len, written)) == expected
        assert len(written) == expected_batches
        assert "Message 40" in [row["text"] for batch in written for row in batch]
        assert ("Message 1" in [row["text"] for batch in written for row in batch]) == (
            count == "all"
        )

    asyncio.run(run())


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


def test_changed_source_during_capture_cannot_write_marker(preparation, monkeypatch):
    server, _, _ = preparation
    original = server.memory_preparation.validate_sources
    changed = False

    async def update(job):
        nonlocal changed
        await original(job)
        if not changed:
            changed = True
            server.history.set_meta("agent", notes="New owner constraint")

    monkeypatch.setattr(server.memory_preparation, "validate_sources", update)
    view = asyncio.run(prepare(server))
    assert view["state"] == "stale_source", view
    assert not server.history.checkpoints("agent")
    assert server.history.session("agent")["runtime"] == "claude-code"


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

    async def provider(harness, model, prompt, schema=None):
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
    assert server.history.checkpoints("agent")[0]["record"]["memory_sources"]
    assert (
        server.history.checkpoints("agent")[0]["record"]["summary_ref"]
        == view["proof"]["revision_id"]
    )


def test_source_failure_does_not_call_any_provider(preparation):
    server, calls, tmp = preparation
    native = transcript(tmp, "agent-claude", "glacier")
    native.unlink()
    view = asyncio.run(prepare(server))
    assert calls == []
    assert not server.history.checkpoints("agent")
    assert "proof" not in view
    assert view["state"] == "incomplete_source", json.dumps(view)


@pytest.mark.parametrize("change", ["touch", "identical_replace", "content", "missing"])
def test_preparation_rechecks_bytes_after_native_file_metadata_changes(
    preparation, monkeypatch, change
):
    server, _, tmp = preparation
    native = transcript(tmp, "agent-claude", "Owner: retain glacier originals.")
    original = server.memory_preparation.validate_sources
    changed = False

    async def rewrite(*args):
        nonlocal changed
        result = await original(*args)
        if not changed:
            changed = True
            if change == "touch":
                st = native.stat()
                os.utime(native, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            elif change == "identical_replace":
                replacement = native.with_suffix(".replacement")
                replacement.write_bytes(native.read_bytes())
                replacement.replace(native)
            elif change == "missing":
                native.unlink()
            else:
                transcript(tmp, "agent-claude", "Owner: a different requirement.")
        return result

    monkeypatch.setattr(server.memory_preparation, "validate_sources", rewrite)
    view = asyncio.run(prepare(server))
    stale = change in {"content", "missing"}
    assert view["state"] == ("stale_source" if stale else "ready"), view
    assert len(server.history.checkpoints("agent")) == (0 if stale else 1)
    assert server.history.session("agent")["runtime"] == "claude-code"


def test_ready_status_revalidates_exact_bytes_without_blocking_the_event_loop(
    preparation, monkeypatch
):
    import threading

    from duckterm import memory_sources

    server, _, _ = preparation

    async def run():
        manager = server.memory_preparation
        view = await prepare(server)
        job = manager.jobs[view["preparation_id"]]
        native = next(s for s in job["sources"] if s.get("fence"))
        path = Path(native["path"])
        before = path.stat()
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        entered, release = threading.Event(), threading.Event()
        original = memory_sources.read_native

        def slow(*args):
            entered.set()
            assert release.wait(5)
            return original(*args)

        monkeypatch.setattr(memory_sources, "read_native", slow)
        task = asyncio.create_task(manager.recheck("agent", job["id"]))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert not task.done()
        finally:
            release.set()
        await task
        after = manager.status("agent", job["id"])
        assert after["state"] == "ready" and after["proof"] == view["proof"]
        assert native["fence"] == memory_sources.file_version(str(path))
        proof = {
            "version": 1,
            "preparation_id": job["id"],
            "snapshot_id": view["proof"]["snapshot_id"],
            "source_generation": view["binding"]["source_generation"],
        }
        # A later same-size rewrite still fails even if mtime is restored.
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b"glacier", b"volcano"))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with pytest.raises(APIError, match="changed|checksum"):
            await manager.checked_proof("agent", proof, "codex", "selected-model")

    asyncio.run(run())


def test_turn_progress_reuses_preparation_revision_without_new_generation(preparation, monkeypatch):
    from test_maintained_progress import provider

    server, _, tmp = preparation
    calls = provider(monkeypatch)

    async def run():
        view = await prepare(server)
        assert view["state"] == "ready" and len(calls) == 2
        checkpoint = server.history.checkpoints("agent")[0]
        assert checkpoint["record"]["summary_ref"] == view["proof"]["revision_id"]
        value = await server.progress_coordinator.refresh("agent")
        assert len(calls) == 2
        assert value["id"] == view["proof"]["revision_id"]
        assert value["continuity"]["verified"]
        assert value["summary_validation"]["ready"]
        assert json.loads(server.history.session("agent")["progress"])["revision_id"] == value["id"]
        assert (
            server.history.checkpoints("agent")[0]["record"]["summary_ref"]
            == view["proof"]["revision_id"]
        )

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
    original = server.memory_preparation.validate_sources

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
        observe("observation-during-preparation")
        return result

    monkeypatch.setattr(server.memory_preparation, "validate_sources", observed)
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready", view
    assert (
        server.history._conn.execute(
            "SELECT count(*) FROM events WHERE id='observation-during-preparation'"
        ).fetchone()[0]
        == 1
    )
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


def test_preparation_reports_real_summary_work_before_readiness(preparation, monkeypatch):
    server, _, _ = preparation
    manager = server.memory_preparation
    original = manager.update
    observed = []

    def record(job, **value):
        original(job, **value)
        observed.append(manager.view(job))

    monkeypatch.setattr(manager, "update", record)
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready", view
    progress = [item["progress"] for item in observed]
    stages = [item["stage"] for item in progress]
    assert stages.index("capturing") < stages.index("writing") < stages.index("reviewing")
    assert stages.index("reviewing") < stages.index("reading") < stages.index("assembling")
    assert stages.index("assembling") < stages.index("checking_sources") < stages.index("ready")
    reviewing = next(item for item in progress if item["stage"] == "reviewing")
    assert reviewing["completed"] == 0
    assert reviewing["total"] > 0
    assert view["progress"]["completed"] == reviewing["total"]
    assert all("proof" not in item for item in observed if item["state"] == "preparing")
    assert all(item["updated_at"] >= item["started_at"] for item in progress)
    assert server.history.session("agent")["runtime"] == "claude-code"


def test_preparation_polling_does_not_invent_progress(preparation, monkeypatch):
    server, _, _ = preparation
    manager = server.memory_preparation
    view = asyncio.run(prepare(server))
    identity = view["preparation_id"]
    before = view["progress"]
    monkeypatch.setattr(
        "duckterm.memory_preparation.time.time", lambda: before["updated_at"] / 1000 + 10
    )
    assert manager.status("agent", identity)["progress"] == before
    manager.update(manager.get("agent", identity), progress={"stage": before["stage"]})
    assert manager.status("agent", identity)["progress"] == before
