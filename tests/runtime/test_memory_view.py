"""The owner's Memory view reads existing verified revisions and exact original evidence."""

import asyncio
import json
import urllib.parse
from types import SimpleNamespace

import pytest
from test_maintained_progress import append, provider
from test_memory import memory_rig, transcript
from test_session_api import dispatch

from duckterm import memory_view
from duckterm.memory_sources import directory

__all__ = ["memory_rig"]


def paginated_fixture(count=100):
    from test_memory_assembly import rows

    revisions = [dict(row, created_at=n) for n, row in enumerate(rows(count))]
    sources = [
        {
            "id": "source",
            "version": "version",
            "kind": "conversation",
            "title": "Originals",
            "records": [
                {"id": f"r{i}", "role": "user", "text": f"Keep constraint {i}"}
                for i in range(count)
            ],
        }
    ]
    for row in revisions:
        claim = row["continuity"]["context"]["constraints"][0]
        sources.append(
            {
                "id": "revision-" + row["id"],
                "version": "v1",
                "kind": "revision",
                "revision_id": row["id"],
                "created_at": row["created_at"],
                "claim_refs": [claim["id"]],
                "records": [
                    {"id": "summary", "role": "derived_summary", "text": row["summary"]},
                    {
                        "id": "constraints:" + claim["id"],
                        "role": "derived_claim",
                        "text": json.dumps(claim),
                    },
                ],
            }
        )
    return {
        "session": "agent",
        "current_revision": revisions[-1]["id"],
        "canonical": {"revisions": revisions, "checkpoints": []},
        "lifecycle": [],
    }, sources


def test_large_memory_view_pages_every_claim_without_loss_or_rewriting():
    catalog, sources = paginated_fixture()
    before = json.dumps([catalog, sources])
    cursor = None
    found = []
    for _ in range(4):
        value = memory_view.project(catalog, sources, None, claims_cursor=cursor)
        assert value["memory"]["state"] == "available"
        page = value["memory"]["pagination"]
        assert page["returned_claims"] == 25 and page["total_claims"] == 100
        found.extend(c["id"] for g in value["memory"]["groups"] for c in g["claims"])
        cursor = page["next_cursor"]
    assert cursor is None and len(found) == len(set(found)) == 100
    assert json.dumps([catalog, sources]) == before
    # A cursor cannot be reused against a different session's otherwise identical memory.
    catalog["session"] = "other"
    from duckterm.core.session_api import APIError

    original, _ = paginated_fixture()
    cursor = memory_view.project(original, sources, None)["memory"]["pagination"]["next_cursor"]
    with pytest.raises(APIError, match="changed"):
        memory_view.project(catalog, sources, None, claims_cursor=cursor)


def test_revision_pages_and_exact_old_revision_are_independent():
    catalog, sources = paginated_fixture(30)
    first = memory_view.project(catalog, sources, catalog["canonical"]["revisions"][0]["id"])
    assert first["revision"]["id"] not in [r["id"] for r in first["revisions"]]
    cursor = first["revisions_pagination"]["next_cursor"]
    second = memory_view.project(catalog, sources, None, revisions_cursor=cursor)
    assert len(second["revisions"]) == 10
    assert len({r["id"] for r in first["revisions"] + second["revisions"]}) == 30
    # A removed revision must invalidate the old continuation.
    sources = [s for s in sources if s.get("revision_id") != first["revisions"][0]["id"]]
    from duckterm.core.session_api import APIError

    with pytest.raises(APIError, match="changed"):
        memory_view.project(catalog, sources, None, revisions_cursor=cursor)


@pytest.mark.parametrize("count,limit,truncated", [(15, 10, True), (30, 100, False)])
def test_revision_truncation_matches_the_requested_page_size(count, limit, truncated):
    catalog, sources = paginated_fixture(count)
    value = memory_view.project(catalog, sources, None, revisions_limit=limit)
    assert value["revisions_truncated"] is truncated
    assert len(value["revisions"]) == min(count, limit)
    assert bool(value["revisions_pagination"]["next_cursor"]) is truncated


@pytest.mark.parametrize("state,pending", [("updated", 0), ("failed", 4), ("failed", 0)])
def test_exact_checkpoint_result_is_attempt_scoped_even_when_failed(state, pending):
    catalog, sources = paginated_fixture(5)
    ids = [r["id"] for r in catalog["canonical"]["revisions"]]
    attempt = {
        "scope": "refresh_attempt",
        "revision_ids": ids[-2:],
        "state": "complete" if state == "updated" else "incomplete",
        "eligible_records": 20,
        "summarized_records": 20 - pending,
        "pending_records": pending,
    }
    update = {
        "state": state,
        "attempt_coverage": attempt,
        "span_coverage": {"revision_ids": ids},
        "summary_corrections": ["Private earlier source text"],
    }
    catalog["canonical"]["checkpoints"] = [
        {
            "id": "checkpoint",
            "created_at": 50,
            "label": "manual",
            "record": {"summary_update": update},
        }
    ]
    sources.append({"kind": "checkpoint", "checkpoint_id": "checkpoint", "records": []})
    result = memory_view.checkpoint(catalog, sources, "checkpoint", limit=1)["checkpoint"]
    assert result["summary_update"]["state"] == state
    assert "summary_corrections" not in result["summary_update"]
    assert result["attempt_coverage"]["pending_records"] == pending
    assert result["batch_coverage_known"] and result["combined_summary"] is None
    assert [b["revision_id"] for b in result["batches"]] == ids[-2:-1]
    following = memory_view.checkpoint(
        catalog, sources, "checkpoint", cursor=result["pagination"]["next_cursor"], limit=1
    )["checkpoint"]
    assert [b["revision_id"] for b in following["batches"]] == ids[-1:]
    # Missing evidence hides the revision instead of leaking cached summary text.
    sources = [s for s in sources if s.get("revision_id") != ids[-1]]
    hidden = memory_view.checkpoint(catalog, sources, "checkpoint")["checkpoint"]["batches"][-1]
    assert hidden == {"revision_id": ids[-1], "state": "unavailable", "revision": None}


def test_old_checkpoint_does_not_mislabel_all_session_revisions_as_this_attempt():
    catalog, sources = paginated_fixture(5)
    catalog["canonical"]["checkpoints"] = [
        {
            "id": "old",
            "created_at": 50,
            "label": "manual",
            "record": {
                "summary_ref": "older-linked",
                "summary_update": {
                    "state": "updated",
                    "span_coverage": {
                        "scope": "unprocessed_selection",
                        "revision_ids": [r["id"] for r in catalog["canonical"]["revisions"]],
                    },
                },
            },
        }
    ]
    sources.append({"kind": "checkpoint", "checkpoint_id": "old", "records": []})
    value = memory_view.checkpoint(catalog, sources, "old")["checkpoint"]
    assert value["batch_coverage_known"] is False and value["batches"] == []
    assert value["linked_revision_id"] == "older-linked"


def get(server, key="agent", action="", **query):
    path = f"/sessions/{key}/memory" + ("/" + action if action else "")
    if query:
        path += "?" + urllib.parse.urlencode(query)
    return dispatch(server, "GET", path, {"x-duckterm-token": server.token})


def claims(view):
    return [claim for group in view["revision"]["groups"] for claim in group["claims"]]


def test_owner_only_read_surface_and_empty_state(memory_rig):
    server, headers, _, _ = memory_rig
    for path in (
        "/sessions/agent/memory",
        "/sessions/agent/memory/read",
        "/sessions/agent/memory/related",
    ):
        assert dispatch(server, "GET", path, {})[0] == 401
        assert dispatch(server, "GET", path, headers)[0] == 403
        assert dispatch(server, "POST", path, {"x-duckterm-token": server.token})[0] == 405
    code, value = get(server)
    assert code == 200 and value["state"] == "empty" and value["revision"] is None
    assert get(server, key="missing")[0] == 404
    assert get(server, unknown="yes")[0] == 400
    assert get(server, checkpoint="missing")[0] == 404
    assert get(server, checkpoint="missing", revision="other")[0] == 400
    assert get(server, claims_limit=0)[0] == 400
    assert get(server, revisions_limit=101)[0] == 400
    assert (
        dispatch(
            server,
            "GET",
            "/sessions/agent/memory?revision=a&revision=b",
            {"x-duckterm-token": server.token},
        )[0]
        == 400
    )


def test_view_and_evidence_reuse_records_without_generation_or_writes(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Original owner constraint <script>not executable</script>")
    calls = provider(monkeypatch)
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    count, changes = len(calls), server.history._conn.total_changes
    code, value = get(server)
    assert code == 200 and value["state"] == "available"
    revision = value["revision"]
    assert revision["id"] == cp["record"]["summary_ref"] == value["current_revision_id"]
    assert revision["summary"] and revision["overview"] and revision["verified_at_save"]
    assert revision["coverage_at_save"]["remaining_records"] == 0
    assert value["latest_checkpoint"]["id"] == cp["id"]
    checkpoint_code, attempt = get(server, checkpoint=cp["id"])
    assert checkpoint_code == 200 and attempt["scope"] == "checkpoint"
    assert attempt["checkpoint"]["id"] == cp["id"]
    assert get(server, key="peer", checkpoint=cp["id"])[0] == 404
    evidence = next(
        e for claim in claims(value) for e in claim["evidence"] if e["kind"] == "conversation"
    )
    code, original = get(
        server, action="read", source=evidence["source"], record=evidence["record"], limit=16
    )
    assert code == 200 and original["next_offset"] == 16
    code, rest = get(
        server, action="read", source=evidence["source"], record=evidence["record"], offset=16
    )
    assert code == 200 and "<script>not executable</script>" in original["text"] + rest["text"]
    code, graph = get(
        server, action="related", source=revision["source"], record=claims(value)[0]["record"]
    )
    assert code == 200 and any(e["relation"] == "supports" for e in graph["relations"])
    assert len(calls) == count and server.history._conn.total_changes == changes
    assert (
        get(
            server, key="peer", action="read", source=evidence["source"], record=evidence["record"]
        )[0]
        == 409
    )
    assert get(server, key="peer", revision=revision["id"])[0] == 404


def test_complete_range_and_historical_span_are_distinct(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Oldest direction")
    # Spans of a few ~1 KB records each.
    monkeypatch.setattr("duckterm.memory_spans.MAX_GENERATION_BYTES", 8000)
    for n in range(12):
        append(path, f"Record {n}: " + "x" * 1000)
    provider(monkeypatch)
    first = asyncio.run(server.progress_coordinator.refresh("agent"))
    append(path, "One more owner direction")
    second = asyncio.run(server.progress_coordinator.refresh("agent"))
    code, latest = get(server)
    assert code == 200 and latest["revision"]["id"] == second["id"]
    coverage = latest["revision"]["coverage_at_save"]
    assert coverage["summarized_records"] == coverage["available_records"] == 1
    assert coverage["remaining_records"] == 0 and latest["revision"]["verified_at_save"]
    assert latest["memory"]["state"] == "available"
    assert latest["memory"]["span_count"] > 2
    assert len(latest["memory"]["groups"][1]["claims"]) == latest["memory"]["span_count"]
    code, older = get(server, revision=first["id"])
    assert code == 200 and not older["revision"]["is_current"]
    assert older["current_revision_id"] == second["id"]
    assert (
        older["revision"]["coverage_at_save"]["summarized_records"]
        == first["continuity"]["summarized_records"]
    )
    assert "memory" not in older


def test_failed_checkpoint_keeps_last_good_summary_visible(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Original constraints")
    provider(monkeypatch)
    good = asyncio.run(server.progress_coordinator.refresh("agent"))
    append(path, "New work")
    monkeypatch.setattr(
        "duckterm.server.summarize",
        lambda _, **kwargs: SimpleNamespace(text="", failure_reason="provider_timeout"),
    )
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    code, value = get(server)
    assert code == 200 and value["revision"]["id"] == good["id"]
    assert value["revision"]["summary"] == good["summary"]
    assert value["latest_checkpoint"]["id"] == cp["id"]
    assert value["latest_checkpoint"]["summary_update"]["reason"] == "provider_timeout"


def test_removed_evidence_hides_claim_and_summary_instead_of_leaking_cache(memory_rig, monkeypatch):
    server, headers, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Unique original task evidence")
    provider(monkeypatch)
    asyncio.run(server.progress_coordinator.refresh("agent"))
    code, before = get(server)
    assert code == 200 and before["state"] == "available"
    citation = next(
        e for claim in claims(before) for e in claim["evidence"] if e["kind"] == "conversation"
    )
    path.unlink()
    for snapshot in directory("agent").glob("*.jsonl"):
        snapshot.unlink()
    server.memory.cache.clear()
    code, after = get(server)
    assert code == 200 and after["state"] == "unavailable"
    assert after["revision"]["summary"] is None and after["revision"]["overview"] is None
    assert after["revision"]["withheld_claims"] > 0 and not claims(after)
    assert get(server, action="read", source=citation["source"], record=citation["record"])[0] in (
        404,
        409,
    )


def test_scope_change_during_projection_cannot_return_stale_memory(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Original task")
    provider(monkeypatch)
    asyncio.run(server.progress_coordinator.refresh("agent"))
    original = memory_view.project

    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        # Run mutation on the event-loop thread once the worker returns.
        return value

    async def read():
        real_thread = asyncio.to_thread

        async def worker(function, *args, **kwargs):
            value = await real_thread(function, *args, **kwargs)
            if function is changed:
                server.history.set_meta("agent", group="Elsewhere")
            return value

        monkeypatch.setattr(memory_view, "project", changed)
        monkeypatch.setattr(asyncio, "to_thread", worker)
        from duckterm.core.session_api import APIError

        with pytest.raises(APIError) as exc:
            await server.memory.operate("agent", "view", {})
        assert exc.value.status == 409

    asyncio.run(read())
