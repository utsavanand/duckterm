import asyncio

from test_memory import memory_rig, transcript
from test_memory_folding import leaf

from duckterm import memory_records
from duckterm.persistence.saved_state import fingerprint, transaction

__all__ = ["memory_rig"]


def test_v1_revision_remains_discoverable_after_scope_bump(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    captured = asyncio.run(server.progress_coordinator.capture("agent"))
    value = leaf(captured, 1)
    value["source"] = {**value["source"], "scope_hash": fingerprint(["agent-memory", "agent"])}
    with transaction(server.history._conn):
        identity = server.digests.add_revision("agent", value, 1)
    with monkeypatch.context() as m:
        m.setattr(  # the v1-only reader
            memory_records, "readable_scopes", lambda key: (fingerprint(["agent-memory", key]),)
        )
        assert identity in {
            r["id"]
            for r in server.memory.catalog("agent", retrieval=True)["canonical"]["revisions"]
        }
    assert server.digests.revision("agent", identity) is not None
    import pytest

    from duckterm import memory_view
    from duckterm.core.session_api import APIError

    assert identity in {
        r["id"] for r in memory_view.revision_headers(server.history._conn, "agent")["revisions"]
    }
    catalog = server.memory.catalog("agent", retrieval=True)
    if identity not in {r["id"] for r in catalog["canonical"]["revisions"]}:
        with pytest.raises(APIError, match="Summary revision is unavailable") as denied:
            memory_view.project(catalog, [], identity)
        assert denied.value.status == 404
    assert identity in {
        r["id"] for r in server.memory.catalog("agent", retrieval=True)["canonical"]["revisions"]
    }


def test_multirange_fold_group_is_first_ten_in_conversation_order():
    from duckterm import memory_compaction
    from duckterm.core import memory_folding

    rows = []
    for n in range(50):
        end = (n + 1) * 100
        ranges = [{"source": "s", "end": end - i} for i in range(40 if n % 2 == 0 else 1)]
        rows.append({"id": str(n), "span": "incremental", "span_input": {"ranges": ranges}})
    heads = memory_folding.active(list(reversed(rows)))
    assert [n["id"] for n in heads] == [str(n) for n in range(50)]
    assert [n["id"] for n in memory_compaction.select(heads, 0)] == [str(n) for n in range(10)]
    assert memory_folding.furthest(list(reversed(rows)))["id"] == "49"


def test_rebuild_keeps_v1_reads_builds_v2_once_and_then_reuses_it(memory_rig, monkeypatch):
    from test_maintained_progress import provider

    from duckterm import memory_view
    from duckterm.core import memory_folding
    from duckterm.persistence.saved_state import memory_scope

    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner work")
    captured = asyncio.run(server.progress_coordinator.capture("agent"))
    old = leaf(captured, 1)
    old["source"] = {**old["source"], "scope_hash": fingerprint(["agent-memory", "agent"])}
    with transaction(server.history._conn):
        v1 = server.digests.add_revision("agent", old, 1)
    calls = provider(monkeypatch)

    first = asyncio.run(server.progress_coordinator.refresh("agent"))
    built = len(calls)
    v2 = [r["id"] for r in memory_folding.rows(server.digests, "agent", memory_scope("agent"))]
    assert built and first["id"] in v2 and v1 not in v2
    # The old summary still opens as an exact historical read.
    catalog = server.memory.catalog("agent", retrieval=True)
    sources, _ = server.memory.materialize(catalog)
    assert memory_view.project(catalog, sources, v1)["revision"]["id"] == v1
    # Current memory is v2 only; v1's span is not counted twice.
    current = memory_view.project(
        catalog, sources, None, items=server.memory.reconciled("agent", catalog)
    )
    assert current["current_revision_id"] == first["id"]
    # An unchanged agent reuses v2: no rebuild and no model calls.
    second = asyncio.run(server.progress_coordinator.refresh("agent"))
    assert second["id"] == first["id"] and len(calls) == built
