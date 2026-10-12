"""Optional folds use the checkpoint transaction and preserve original revisions."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_memory import memory_rig, transcript

from duckterm import memory_contract, memory_summary
from duckterm.core import memory_folding as folding
from duckterm.persistence.saved_state import current_revision, transaction

__all__ = ["memory_rig"]


def leaf(captured, n, *, shared=False):
    text = "Keep the owner constraint" if shared else f"Keep owner constraint {n}"
    refs = ["source:version:record" if shared else f"source:version:r{n}"]
    context = {"overview": f"Work {n}.", **{f: [] for f in memory_summary.FIELDS}}
    context["constraints"] = [
        {
            "text": text,
            "refs": refs,
            "kind": "constraint",
            "state": "open",
            "id": memory_contract.claim_id("constraints", text, refs),
        }
    ]
    return {
        **{k: captured[k] for k in ("source", "conversation", "policy")},
        "source_at": n,
        "summary": context["overview"],
        "input_key": str(n),
        "span": "incremental",
        "context_version": 2,
        "coverage": {"declared": True, "unknown": False, "omitted": []},
        "continuity": {"context": context, "verified": True},
        "summary_validation": {"ready": True, "reason_codes": []},
    }


def seed(server, captured, count=50, *, key="agent", shared=False):
    with transaction(server.history._conn):
        return [
            server.digests.add_revision(key, leaf(captured, n, shared=shared), n)
            for n in range(count)
        ]


def provider(server, monkeypatch, *, fail=False):
    calls = []

    def generate(prompt):
        assert not server.history._conn.in_transaction
        calls.append(prompt)
        if fail:
            return SimpleNamespace(text="", failure_reason="provider_timeout")
        value = (
            {"ready": True, "reason_codes": []}
            if prompt.startswith("Validate")
            else {"overview": "Combined work."}
        )
        return SimpleNamespace(text=json.dumps(value))

    monkeypatch.setattr("duckterm.server.summarize", generate)
    return calls


def save(server, captured, value, proposal, *, checkpoint=True):
    return server.progress_coordinator.persist(
        "agent",
        captured,
        None,
        value,
        {"summary": value["summary"]},
        [],
        [],
        folding=proposal,
        checkpoint={"id": "test-checkpoint", "label": "manual", "git": {}} if checkpoint else None,
    )


def test_fifty_first_leaf_commits_reference_parent_and_checkpoint_atomically(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    calls = provider(server, monkeypatch)

    async def run():
        captured = await server.progress_coordinator.capture("agent")
        identities = seed(server, captured)
        originals = [server.digests.revision("agent", identity) for identity in identities]
        incoming = leaf(captured, 51)
        proposal = await folding.prepare(server.progress_coordinator, "agent", captured, incoming)
        assert len(calls) == 2 and len(proposal["parents"]) == 1
        saved = save(server, captured, incoming, proposal)
        assert saved["compaction"]["state"] == "compacted"
        parent_id = saved["compaction"]["parent_ids"][0]
        rows = folding.rows(server.digests, "agent", captured["source"]["scope_hash"])
        active = folding.active(rows)
        assert sum(n["tier"] == 0 for n in active) == 41
        assert sum(n["tier"] == 1 for n in active) == 1
        assert [server.digests.revision("agent", i) for i in identities] == originals
        parent = server.digests.revision("agent", parent_id)
        assert parent["fold"]["children"] == identities[:10]
        assert all(
            set(c) == {"id", "ref_revision"} for c in parent["continuity"]["context"]["constraints"]
        )
        context, _ = memory_contract.resolve_revision(parent, {r["id"]: r for r in rows}.get)
        assert context["constraints"] == [
            r["continuity"]["context"]["constraints"][0] for r in originals[:10]
        ]
        record = json.loads(
            server.history._conn.execute(
                "SELECT record_json FROM checkpoints WHERE id='test-checkpoint'"
            ).fetchone()[0]
        )
        assert record["summary_ref"] == saved["id"]
        assert current_revision(server.history.session("agent")) == saved["id"] != parent_id

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["below_threshold", "cumulative", "other_session", "other_scope"])
def test_ineligible_history_never_starts_provider(memory_rig, monkeypatch, mode):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    calls = provider(server, monkeypatch)

    async def run():
        captured = await server.progress_coordinator.capture("agent")
        values = [leaf(captured, n) for n in range(49 if mode == "below_threshold" else 50)]
        for value in values:
            if mode == "cumulative":
                value.pop("span")
            if mode == "other_scope":
                value["source"] = {"scope_hash": "revoked"}
        with transaction(server.history._conn):
            for n, value in enumerate(values):
                server.digests.add_revision(
                    "peer" if mode == "other_session" else "agent", value, n
                )
        assert (
            await folding.prepare(
                server.progress_coordinator, "agent", captured, leaf(captured, 51)
            )
            is None
        )
        assert calls == []

    asyncio.run(run())


def test_provider_failure_saves_checkpoint_and_defers_repeat_cost(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    calls = provider(server, monkeypatch, fail=True)

    async def run():
        captured = await server.progress_coordinator.capture("agent")
        seed(server, captured)
        incoming = leaf(captured, 51)
        proposal = await folding.prepare(server.progress_coordinator, "agent", captured, incoming)
        assert proposal["state"] == "pending" and len(calls) == 1
        saved = save(server, captured, incoming, proposal)
        assert saved["summary_validation"]["ready"]
        assert (
            len(
                folding.active(
                    folding.rows(server.digests, "agent", captured["source"]["scope_hash"])
                )
            )
            == 51
        )
        retry = await folding.prepare(
            server.progress_coordinator, "agent", captured, leaf(captured, 52)
        )
        assert retry["attempts"][0]["reason"] == "retry_deferred" and len(calls) == 1
        with monkeypatch.context() as m:
            m.setattr(
                folding.time, "time", lambda: proposal["attempts"][0]["retry_after"] / 1000 + 1
            )
            await folding.prepare(
                server.progress_coordinator, "agent", captured, leaf(captured, 52)
            )
        assert len(calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["source_changed", "persistence_failed"])
def test_optional_parent_failure_preserves_leaf_and_checkpoint(memory_rig, monkeypatch, mode):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    provider(server, monkeypatch)

    async def run():
        captured = await server.progress_coordinator.capture("agent")
        seed(server, captured)
        incoming = leaf(captured, 51)
        proposal = await folding.prepare(server.progress_coordinator, "agent", captured, incoming)
        if mode == "source_changed":
            seed(server, captured, 1)
        else:
            original = server.digests.add_revision
            # Insert one optional parent, then fail the second: savepoint must
            # remove the first without rolling back the incoming checkpoint.
            proposal["parents"].append(deepcopy(proposal["parents"][0]))
            count = 0

            def fail_second(key, value, now):
                nonlocal count
                if "fold" in value:
                    count += 1
                    if count == 2:
                        raise RuntimeError("test insertion failed")
                return original(key, value, now)

            monkeypatch.setattr(server.digests, "add_revision", fail_second)
        saved = save(server, captured, incoming, proposal)
        assert saved["compaction"]["state"] == "pending"
        assert saved["compaction"]["reason"] == mode
        assert "parent_ids" not in saved["compaction"]
        assert all(a["state"] == "pending" for a in saved["compaction"]["attempts"])
        assert not any(
            r.get("fold")
            for r in folding.rows(server.digests, "agent", captured["source"]["scope_hash"])
        )
        assert (
            server.history._conn.execute(
                "SELECT count(*) FROM checkpoints WHERE id='test-checkpoint'"
            ).fetchone()[0]
            == 1
        )

    asyncio.run(run())


def test_cascade_folds_old_tier_one_before_appending_new_parent(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    calls = provider(server, monkeypatch)

    async def run():
        captured = await server.progress_coordinator.capture("agent")
        leaves = seed(server, captured, 550, shared=True)
        parents = []
        with transaction(server.history._conn):
            for n in range(50):
                value = leaf(captured, 1000 + n, shared=True)
                value["fold"] = {"tier": 1, "children": leaves[n * 10 : (n + 1) * 10]}
                parents.append(server.digests.add_revision("agent", value, 1000 + n))
        incoming = leaf(captured, 2000, shared=True)
        proposal = await folding.prepare(server.progress_coordinator, "agent", captured, incoming)
        assert len(calls) == 4
        assert [p["fold"]["tier"] for p in proposal["parents"]] == [2, 1]
        assert proposal["parents"][0]["fold"]["children"] == parents[:10]
        saved = save(server, captured, incoming, proposal)
        assert len(saved["compaction"]["parent_ids"]) == 2
        heads = folding.active(
            folding.rows(server.digests, "agent", captured["source"]["scope_hash"])
        )
        assert [sum(h["tier"] == tier for h in heads) for tier in (0, 1, 2)] == [41, 41, 1]

    asyncio.run(run())


def test_checkpoint_insert_failure_rolls_back_parent_links_too(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    provider(server, monkeypatch)

    async def run():
        captured = await server.progress_coordinator.capture("agent")
        seed(server, captured)
        before = folding.rows(server.digests, "agent", captured["source"]["scope_hash"])
        incoming = leaf(captured, 51)
        proposal = await folding.prepare(server.progress_coordinator, "agent", captured, incoming)
        with pytest.raises(KeyError):
            server.progress_coordinator.persist(
                "agent",
                captured,
                None,
                incoming,
                {"summary": incoming["summary"]},
                [],
                [],
                folding=proposal,
                checkpoint={"git": {}},
            )
        assert folding.rows(server.digests, "agent", captured["source"]["scope_hash"]) == before
        assert current_revision(server.history.session("agent")) is None

    asyncio.run(run())


def test_unreadable_claim_reference_leaves_checkpoint_usable(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    calls = provider(server, monkeypatch)

    async def run():
        captured = await server.progress_coordinator.capture("agent")
        bad = leaf(captured, 0)
        bad["continuity"]["context"]["constraints"] = [
            {"id": "missing", "ref_revision": "unauthorized"}
        ]
        with transaction(server.history._conn):
            server.digests.add_revision("agent", bad, -1)
        seed(server, captured, 49)
        incoming = leaf(captured, 51)
        proposal = await folding.prepare(server.progress_coordinator, "agent", captured, incoming)
        assert proposal["attempts"][0]["reason"] == "unreadable_fold_input"
        saved = save(server, captured, incoming, proposal)
        assert saved["summary_validation"]["ready"] and calls == []

    asyncio.run(run())


def test_changes_during_optional_provider_work_never_promote_new_revision(memory_rig, monkeypatch):
    from test_maintained_progress import append
    from test_maintained_progress import provider as summary_provider

    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Owner work")
    summary_provider(monkeypatch)

    async def change_source(*args):
        append(path, "New owner direction arrived during compaction")
        return {"state": "pending", "reason": "provider_failed"}

    monkeypatch.setattr(folding, "prepare", change_source)
    result = asyncio.run(server.progress_coordinator.refresh_result("agent"))
    assert result.state == "failed" and result.reason == "source_changed"
    assert current_revision(server.history.session("agent")) is None


def test_cancellation_before_write_creates_no_parent_or_checkpoint(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")

    async def run():
        started = asyncio.Event()

        async def slow(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(folding.memory_compaction, "compact", slow)
        captured = await server.progress_coordinator.capture("agent")
        seed(server, captured)
        before = folding.rows(server.digests, "agent", captured["source"]["scope_hash"])
        task = asyncio.create_task(
            folding.prepare(server.progress_coordinator, "agent", captured, leaf(captured, 51))
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert folding.rows(server.digests, "agent", captured["source"]["scope_hash"]) == before

    asyncio.run(run())


def test_rejected_summary_is_never_certified_by_folding(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    calls = provider(server, monkeypatch)

    async def run():
        captured = await server.progress_coordinator.capture("agent")
        bad = leaf(captured, 0)
        bad["summary_validation"] = {"ready": False, "reason_codes": ["summary_unverified"]}
        with transaction(server.history._conn):
            server.digests.add_revision("agent", bad, -1)
        seed(server, captured, 49)
        proposal = await folding.prepare(
            server.progress_coordinator, "agent", captured, leaf(captured, 51)
        )
        assert proposal["state"] == "pending" and proposal["parents"] == []
        assert calls == []

    asyncio.run(run())
