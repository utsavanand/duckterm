"""Independent span writes retain evidence and never masquerade as cumulative memory."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_maintained_progress import append
from test_memory import memory_rig, transcript

from duckterm import memory_continuity, memory_spans, memory_summary
from duckterm.core import memory_folding
from duckterm.llm.summarizer import Summary
from duckterm.persistence.saved_state import current_revision

__all__ = ["memory_rig"]


def snapshot(texts):
    return {
        "policy": "test-policy",
        "source": {"scope_hash": "test-scope"},
        "memory_sources": [
            {
                "id": "test-conversation",
                "version": str(len(texts)),
                "kind": "conversation",
                "current": True,
                "records": [
                    {"id": str(i), "role": "user", "text": text} for i, text in enumerate(texts)
                ],
            }
        ],
    }


def test_successive_spans_advance_first_without_reusing_prior_summary():
    first = snapshot(["Earlier owner decision"])
    origin = memory_spans.boundary(first, empty=True)
    a = memory_spans.plan(first, origin)
    second = snapshot(["Earlier owner decision", "New owner correction"])
    b = memory_spans.plan(second, a["next_boundary"])
    assert a["ranges"][0]["first"] == 0
    assert b["ranges"][0]["first"] == 1
    assert b["ranges"][0]["end"] == 2
    prompt = memory_spans.prompt(b)
    assert "Earlier owner decision" not in prompt
    assert "New owner correction" in prompt
    assert b["prior_context"] is None
    assert memory_spans.continuity(b, {})["summarized_records"] == 1
    assert memory_spans.metadata(b)["coverage"]["omitted"] == []


def test_frozen_large_span_splits_in_bytes_and_turns_without_holes():
    captured = snapshot([f"Turn {i}: " + "🔒" * 2000 for i in range(12)])
    lower = memory_spans.boundary(captured, empty=True)
    seen = []
    upper = memory_spans.boundary(captured)
    for _ in range(12):
        batch = memory_spans.plan(captured, lower)
        assert batch["upper"] == upper
        assert len(memory_spans.prompt(batch).encode()) <= memory_spans.MAX_GENERATION_BYTES
        assert len(batch["selected"]) <= memory_spans.MAX_RECORDS
        assert not batch["oversized"]
        seen += [r["record"] for r in batch["selected"]]
        lower = batch["next_boundary"]
        if not batch["pending_records"]:
            break
    assert seen == [str(i) for i in range(12)]
    assert lower == upper
    tiny = snapshot(["x"] * 51)
    batch = memory_spans.plan(tiny, memory_spans.boundary(tiny, empty=True))
    assert len(batch["selected"]) == 50 and batch["pending_records"] == 1


def test_oversized_record_remains_pending_and_does_not_skip_to_later_turn():
    captured = snapshot(["First", "x" * 48000, "Third"])
    a = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
    assert [r["text"] for r in a["selected"]] == ["First"]
    assert a["pending_records"] == 2
    b = memory_spans.plan(captured, a["next_boundary"])
    assert not b["selected"] and b["oversized"]
    assert b["pending_records"] == 2
    assert b["next_boundary"] == a["next_boundary"]
    assert len(b["pending_refs"]) == 2


@pytest.mark.parametrize("texts,reason", [(["rewritten"], "rewritten"), ([], "removed")])
def test_rewritten_or_deleted_prefix_refuses_instead_of_inventing_a_boundary(texts, reason):
    old = memory_spans.boundary(snapshot(["Original"]))
    with pytest.raises(ValueError, match="span_source_" + reason):
        memory_spans.plan(snapshot(texts), old)


def test_cumulative_revision_is_not_accepted_as_span_boundary():
    captured = snapshot(["Original"])
    with pytest.raises(ValueError, match="explicit_span_boundary_required"):
        memory_spans.plan(captured, {"span": "cumulative", "continuity": {"frontiers": {}}})
    lower = memory_spans.boundary(captured)
    lower["scope"] = "another-folder"
    with pytest.raises(ValueError, match="explicit_span_boundary_required"):
        memory_spans.plan(captured, lower)


def test_reference_only_originals_advance_boundary_without_entering_prompt():
    captured = snapshot(["Owner instruction"])
    source = captured["memory_sources"][0]
    source["records"].append({"id": "tool", "role": "tool", "text": "PRIVATE SUCCESS BODY"})
    for kind in ("PermissionRequest", "PostToolUse", "PostToolUseFailure"):
        captured["memory_sources"].append(
            {
                "id": kind,
                "kind": "event",
                "version": "v1",
                "records": [{"id": "0", "role": "event", "text": json.dumps({"event_type": kind})}],
            }
        )
    batch = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
    text = memory_spans.prompt(batch)
    assert "PRIVATE SUCCESS BODY" not in text
    assert '"source": "PermissionRequest"' not in text
    assert '"source": "PostToolUse"' not in text
    assert '"source": "PostToolUseFailure"' in text
    assert batch["next_boundary"] == batch["upper"]


def provider(monkeypatch):
    calls = []
    fail = [False]

    def generate(prompt):
        calls.append(prompt)
        assert len(prompt.encode()) <= memory_spans.MAX_INPUT_BYTES
        if fail[0]:
            return Summary("", "none", "provider_timeout")
        if prompt.startswith("You are validating"):
            return SimpleNamespace(
                text=json.dumps(
                    {"accept": [], "summary_validation": {"ready": True, "reason_codes": []}}
                )
            )
        records = json.loads(prompt.split("MEMORY UPDATE:\n", 1)[1])["records"]
        value = {"overview": "Keep the owner constraint.", **{k: [] for k in memory_summary.FIELDS}}
        value["constraints"] = [
            {
                "text": "Keep the owner constraint.",
                "refs": [records[0]["ref"]],
                "kind": "constraint",
                "state": "open",
            }
        ]
        return SimpleNamespace(
            text=json.dumps({"summary": "Keep the owner constraint.", "context": value})
        )

    monkeypatch.setattr("duckterm.server.summarize", generate)
    return calls, fail


async def capture(server):
    captured = await server.progress_coordinator.capture("agent")
    return await memory_continuity.capture(server, "agent", captured)


def test_shared_writer_saves_typed_leaf_and_reuses_it_without_promoting_handoff(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "First owner instruction")
    calls, _ = provider(monkeypatch)

    async def run():
        initial_progress = server.history.session("agent")["progress"]
        frozen = await capture(server)
        lower = memory_spans.boundary(frozen, empty=True)
        first, a = await server.progress_coordinator.write_span("agent", frozen, lower)
        assert first.state == "updated"
        leaf = first.revision
        assert leaf["span"] == "incremental" and leaf["context_version"] == 2
        claim = leaf["continuity"]["context"]["constraints"][0]
        assert len(claim["id"]) == 16 and claim["state"] == "open"
        assert leaf["memory_sources"]
        assert server.history.session("agent")["progress"] == initial_progress
        assert current_revision(server.history.session("agent")) is None
        reused, _ = await server.progress_coordinator.write_span("agent", frozen, lower)
        assert reused.state == "reused" and reused.revision["id"] == leaf["id"]
        assert len(calls) == 2
        append(path, "Second owner instruction")
        updated = await capture(server)
        second, b = await server.progress_coordinator.write_span(
            "agent", updated, a["next_boundary"]
        )
        assert second.state == "updated" and len(calls) == 4
        assert "First owner instruction" not in calls[2]
        assert b["ranges"][0]["first"] == 1
        assert server.digests.revision("agent", leaf["id"]) == leaf
        heads = memory_folding.active(
            memory_folding.rows(server.digests, "agent", frozen["source"]["scope_hash"])
        )
        assert len(heads) == 2

    asyncio.run(run())


def test_failed_subrange_keeps_previous_success_reusable_and_no_overlap(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "First " + "x" * 22000)
    append(path, "Second " + "y" * 22000)
    calls, fail = provider(monkeypatch)

    async def run():
        frozen = await capture(server)
        lower = memory_spans.boundary(frozen, empty=True)
        first, a = await server.progress_coordinator.write_span("agent", frozen, lower)
        assert first.state == "updated" and a["pending_records"] == 1
        saved = deepcopy(first.revision)
        fail[0] = True
        failed, b = await server.progress_coordinator.write_span(
            "agent", frozen, a["next_boundary"]
        )
        assert failed.state == "failed" and failed.revision is None
        assert server.digests.find_revision("agent", b["input_key"]) is None
        assert server.digests.revision("agent", saved["id"]) == saved
        fail[0] = False
        reused, _ = await server.progress_coordinator.write_span("agent", frozen, lower)
        assert reused.state == "reused" and len(calls) == 3
        second, _ = await server.progress_coordinator.write_span(
            "agent", frozen, a["next_boundary"]
        )
        assert second.state == "updated" and len(calls) == 5
        assert b["ranges"][0]["first"] == a["ranges"][0]["end"]
        assert second.revision["continuity"]["summarized_records"] == 1

    asyncio.run(run())


def test_changed_source_during_generation_does_not_save_leaf(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Original owner instruction")
    calls, _ = provider(monkeypatch)
    from duckterm import server as server_module

    original = server_module.summarize

    def changed(prompt):
        result = original(prompt)
        if len(calls) == 1:
            append(path, "New instruction while generating")
        return result

    monkeypatch.setattr(server_module, "summarize", changed)

    async def run():
        frozen = await capture(server)
        outcome, batch = await server.progress_coordinator.write_span(
            "agent", frozen, memory_spans.boundary(frozen, empty=True)
        )
        assert outcome.state == "failed" and outcome.reason == "source_changed"
        assert server.digests.find_revision("agent", batch["input_key"]) is None

    asyncio.run(run())


def test_saved_leaf_evidence_remains_readable_by_its_exact_version(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Original exact evidence")
    provider(monkeypatch)

    async def run():
        frozen = await capture(server)
        outcome, _ = await server.progress_coordinator.write_span(
            "agent", frozen, memory_spans.boundary(frozen, empty=True)
        )
        claim = outcome.revision["continuity"]["context"]["constraints"][0]
        source, version, record = claim["refs"][0].split(":", 2)
        append(path, "Later evidence")
        read = await server.memory.operate(
            "agent", "read", {"source": [source + ":" + version], "record": [record]}
        )
        assert "Original exact evidence" in read["text"]

    asyncio.run(run())


def test_simultaneous_same_span_writes_join_without_duplicate_provider_calls(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Shared owner constraint")
    calls, _ = provider(monkeypatch)

    async def run():
        frozen = await capture(server)
        lower = memory_spans.boundary(frozen, empty=True)
        a, b = await asyncio.gather(
            server.progress_coordinator.write_span("agent", frozen, lower),
            server.progress_coordinator.write_span("agent", frozen, lower),
        )
        assert a[0].revision["id"] == b[0].revision["id"]
        assert len(calls) == 2

    asyncio.run(run())


def test_cancelled_waiter_does_not_cancel_a_span_write(memory_rig, monkeypatch):
    import threading

    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Shared owner constraint")
    calls, _ = provider(monkeypatch)
    from duckterm import server as server_module

    generate = server_module.summarize
    entered, released = threading.Event(), threading.Event()

    def paused(prompt):
        if not entered.is_set():
            entered.set()
            assert released.wait(3)
        return generate(prompt)

    monkeypatch.setattr(server_module, "summarize", paused)

    async def run():
        coordinator = server.progress_coordinator
        frozen = await capture(server)
        lower = memory_spans.boundary(frozen, empty=True)
        waiter = asyncio.create_task(coordinator.write_span("agent", frozen, lower))
        assert await asyncio.to_thread(entered.wait, 3)
        writer = coordinator.active["agent"]
        waiter.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await waiter
        finally:
            released.set()
        assert (await writer).state == "updated"
        await asyncio.sleep(0)
        assert "agent" not in coordinator.active
        reused, _ = await coordinator.write_span("agent", frozen, lower)
        assert reused.state == "reused" and len(calls) == 2

    asyncio.run(run())


def test_changed_snapshot_does_not_allow_rewriting_an_already_committed_span(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "First owner instruction")
    calls, _ = provider(monkeypatch)

    async def run():
        frozen = await capture(server)
        lower = memory_spans.boundary(frozen, empty=True)
        first, _ = await server.progress_coordinator.write_span("agent", frozen, lower)
        assert first.state == "updated"
        append(path, "Appended owner instruction")
        newer = await capture(server)
        overlap, _ = await server.progress_coordinator.write_span("agent", newer, lower)
        assert overlap.state == "failed" and overlap.diagnostic == "overlapping_span"
        assert len(calls) == 2

    asyncio.run(run())
