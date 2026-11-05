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


def first_record_limit(texts):
    """A span byte limit that admits one of these short records, never two."""
    alone = snapshot(texts[:1])
    alone["memory_sources"][0]["version"] = str(len(texts))
    batch = memory_spans.plan(alone, memory_spans.boundary(alone, empty=True))
    return len(memory_spans.prompt(batch).encode()) + 30


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


def test_proposal_before_batch_boundary_remains_context_without_advancing_coverage():
    captured = snapshot(["Use the real dashboard; keep the voice. Shall I proceed?", "sgtm"])
    captured["memory_sources"][0]["records"][0]["role"] = "assistant"
    rows = memory_spans.original_rows(captured)
    captured["checkpoint_selection"] = {(rows[1]["source"], rows[1]["record"]): rows[1]["hash"]}
    b = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
    payload = json.loads(memory_spans.prompt(b).split("MEMORY UPDATE:\n", 1)[1])
    assert [r["text"] for r in payload["records"]] == ["sgtm"]
    assert [r["record"] for r in payload["context_records"]] == ["0"]
    # Context records are numbered first, then the selected records.
    assert [r["ref"] for r in payload["context_records"]] == ["#0"]
    assert [r["ref"] for r in payload["records"]] == ["#1"]
    assert b["ranges"] == [{"source": "test-conversation", "version": "2", "first": 1, "end": 2}]
    assert memory_spans.continuity(b, {})["processed"] == {
        "test-conversation": {"1": b["selected"][0]["hash"]}
    }
    context = {
        "overview": "The owner approved the real-dashboard proposal.",
        **{field: [] for field in memory_summary.FIELDS},
    }
    context["decisions"] = [
        {
            "text": "Proceed with the real dashboard and keep the voice.",
            "refs": ["#0", "#1"],
        }
    ]
    decision = memory_continuity.context(json.dumps({"context": context}), b)["decisions"][0]
    assert decision["refs"] == ["test-conversation:2:0", "test-conversation:2:1"]
    assert decision["kind"] == "decision" and decision["state"] == "open"
    assert memory_spans.metadata(b)["span_input"]["context_refs"] == ["test-conversation:2:0"]


def test_short_refs_map_back_and_full_refs_and_explicit_kinds_pass_through():
    captured = snapshot(["Owner: keep originals.", "Assistant: shipped #41."])
    batch = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
    context = {"overview": "Originals are kept.", **{f: [] for f in memory_summary.FIELDS}}
    context["constraints"] = [
        {"text": "Keep originals.", "refs": "#0"},
        {"text": "Shipping lesson.", "refs": ["test-conversation:2:1"], "kind": "learning"},
    ]
    context["questions"] = [{"text": "Is #41 released?", "refs": ["#1"]}]
    parsed = memory_continuity.context(json.dumps({"context": context}), batch)
    assert [(c["refs"], c["kind"], c["state"]) for c in parsed["constraints"]] == [
        (["test-conversation:2:0"], "constraint", "open"),
        (["test-conversation:2:1"], "learning", "open"),
    ]
    assert parsed["questions"][0]["refs"] == ["test-conversation:2:1"]
    assert parsed["questions"][0]["kind"] == "question"
    context["questions"] = [{"text": "Unknown ref.", "refs": ["#9"]}]
    with pytest.raises(memory_summary.InvalidContext) as rejected:
        memory_continuity.context(json.dumps({"context": context}), batch)
    assert rejected.value.reason == "unknown_reference"


def test_context_never_skips_a_large_proposal_or_reduces_selected_records(monkeypatch):
    captured = snapshot(["Earlier unrelated proposal", "x" * 48000, "sgtm"])
    captured["memory_sources"][0]["records"][1]["role"] = "assistant"
    rows = memory_spans.original_rows(captured)
    captured["checkpoint_selection"] = {(rows[2]["source"], rows[2]["record"]): rows[2]["hash"]}
    # Even clipped, the large proposal does not fit beside the selected record.
    monkeypatch.setattr(memory_spans, "MAX_GENERATION_BYTES", 8000)
    batch = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
    assert [r["text"] for r in batch["selected"]] == ["sgtm"]
    assert batch["context_records"] == []
    assert not batch["oversized"]
    assert len(memory_spans.prompt(batch).encode()) <= memory_spans.MAX_GENERATION_BYTES


def test_frozen_large_span_splits_in_bytes_without_holes():
    captured = snapshot([f"Turn {i}: " + "🔒" * 2000 for i in range(12)])
    lower = memory_spans.boundary(captured, empty=True)
    seen = []
    batches = 0
    upper = memory_spans.boundary(captured)
    for _ in range(12):
        batch = memory_spans.plan(captured, lower)
        batches += 1
        assert batch["upper"] == upper
        assert len(memory_spans.prompt(batch).encode()) <= memory_spans.MAX_GENERATION_BYTES
        assert not batch["oversized"]
        seen += [r["record"] for r in batch["selected"]]
        lower = batch["next_boundary"]
        if not batch["pending_records"]:
            break
    assert seen == [str(i) for i in range(12)]
    assert batches >= 2
    assert lower == upper
    # Many small records are one span: only bytes bound a span, not a count.
    tiny = snapshot(["x"] * 51)
    batch = memory_spans.plan(tiny, memory_spans.boundary(tiny, empty=True))
    assert len(batch["selected"]) == 51 and batch["pending_records"] == 0


def test_long_record_is_clipped_into_the_span_instead_of_failing_as_oversized():
    middle = "MIDDLE-OF-LOG"
    log = "head " + "a" * 30000 + middle + "b" * 30000 + " tail"
    captured = snapshot(["First", log, "Third"])
    batch = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
    assert [r["record"] for r in batch["selected"]] == ["0", "1", "2"]
    assert not batch["oversized"] and batch["pending_records"] == 0
    prompt = memory_spans.prompt(batch)
    assert len(prompt.encode()) <= memory_spans.MAX_GENERATION_BYTES
    assert middle not in prompt and "head " in prompt and " tail" in prompt
    assert "middle omitted" in prompt
    clipped = memory_spans.clip(log)
    assert len(clipped) < memory_spans.MAX_RECORD_CHARS + 100
    assert memory_spans.clip("short") == "short"


def test_oversized_record_remains_pending_and_does_not_skip_to_later_turn(monkeypatch):
    monkeypatch.setattr(memory_spans, "MAX_GENERATION_BYTES", 8000)
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

    def generate(prompt, **kwargs):
        calls.append(prompt)
        assert len(prompt.encode()) <= memory_spans.repair_input_limit()
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
        # One writer call per span; spans are not model-reviewed.
        assert len(calls) == 1
        assert leaf["summary_review"] == {"state": "not_reviewed", "reason_codes": []}
        append(path, "Second owner instruction")
        updated = await capture(server)
        second, b = await server.progress_coordinator.write_span(
            "agent", updated, a["next_boundary"]
        )
        assert second.state == "updated" and len(calls) == 2
        assert "First owner instruction" not in calls[1]
        assert b["ranges"][0]["first"] == 1
        assert server.digests.revision("agent", leaf["id"]) == leaf
        heads = memory_folding.active(
            memory_folding.rows(server.digests, "agent", frozen["source"]["scope_hash"])
        )
        assert len(heads) == 2

    asyncio.run(run())


def test_failed_subrange_keeps_previous_success_reusable_and_no_overlap(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    monkeypatch.setattr(memory_spans, "MAX_GENERATION_BYTES", 16000)
    path = transcript(tmp, "agent-claude", "First " + "x" * 11000)
    append(path, "Second " + "y" * 11000)
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
        assert reused.state == "reused" and len(calls) == 2
        second, _ = await server.progress_coordinator.write_span(
            "agent", frozen, a["next_boundary"]
        )
        assert second.state == "updated" and len(calls) == 3
        assert b["ranges"][0]["first"] == a["ranges"][0]["end"]
        assert second.revision["continuity"]["summarized_records"] == 1

    asyncio.run(run())


def test_changed_source_during_generation_does_not_save_leaf(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Original owner instruction")
    calls, _ = provider(monkeypatch)
    from duckterm import server as server_module

    original = server_module.summarize

    def changed(prompt, **_kw):
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
        assert len(calls) == 1

    asyncio.run(run())


def test_cancelled_waiter_does_not_cancel_a_span_write(memory_rig, monkeypatch):
    import threading

    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Shared owner constraint")
    calls, _ = provider(monkeypatch)
    from duckterm import server as server_module

    generate = server_module.summarize
    entered, released = threading.Event(), threading.Event()

    def paused(prompt, **_kw):
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
        (writer,) = coordinator.pending.values()
        waiter.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await waiter
        finally:
            released.set()
        assert (await writer).state == "updated"
        await asyncio.sleep(0)
        assert not coordinator.pending
        reused, _ = await coordinator.write_span("agent", frozen, lower)
        assert reused.state == "reused" and len(calls) == 1

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
        assert len(calls) == 1

    asyncio.run(run())


def test_selected_window_metadata_does_not_grow_with_unrelated_event_history():
    sizes = []
    for count in (100, 10000):
        captured = snapshot([f"Owner decision {i}: retain original evidence." for i in range(20)])
        captured["memory_sources"] += [
            {
                "id": f"event-{i:032x}",
                "version": "e" * 64,
                "kind": "event",
                "records": [
                    {
                        "id": "0",
                        "role": "event",
                        "text": json.dumps({"event_type": "PostToolUse", "output": "tool body"}),
                    }
                ],
            }
            for i in range(count)
        ]
        rows = memory_spans.original_rows(captured)
        captured["checkpoint_selection"] = {
            (r["source"], r["record"]): r["hash"] for r in rows if r["kind"] == "conversation"
        }
        plan = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
        metadata = memory_spans.metadata(plan)
        saved_input = metadata["span_input"]
        assert saved_input["selection_window"] is True
        assert saved_input["input_hash"] == plan["input_key"]
        assert not {"lower", "upper", "next_boundary"} & saved_input.keys()
        assert len(plan["selected"]) == 20
        assert memory_spans.overlaps(plan, metadata)
        assert memory_spans.overlaps(plan, {**metadata, "span_input": {"ranges": plan["ranges"]}})
        # Evidence remains exact; the compact receipt neither claims old tools
        # as summarized nor rewrites the frozen in-memory iteration boundary.
        assert memory_spans.continuity(plan, {})["summarized_records"] == 20
        assert len(plan["next_boundary"]["sources"]) == count + 1
        sizes.append(len(json.dumps(metadata).encode()))
    assert sizes[0] == sizes[1]


def test_sequential_metadata_keeps_the_exact_retry_boundary(monkeypatch):
    texts = ["First record", "Second record"]
    captured = snapshot(texts)
    monkeypatch.setattr(memory_spans, "MAX_GENERATION_BYTES", first_record_limit(texts))
    plan = memory_spans.plan(captured, memory_spans.boundary(captured, empty=True))
    saved = memory_spans.metadata(plan)["span_input"]
    assert saved["next_boundary"] == plan["next_boundary"]
    assert "lower" not in saved and "upper" not in saved
    next_plan = memory_spans.plan(captured, saved["next_boundary"])
    assert [r["record"] for r in next_plan["selected"]] == ["1"]
