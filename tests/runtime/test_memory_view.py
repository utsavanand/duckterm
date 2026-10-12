"""The owner's Memory view reads existing verified revisions and exact original evidence."""

import asyncio
import urllib.parse
from types import SimpleNamespace

import pytest
from test_maintained_progress import append, provider
from test_memory import memory_rig, transcript
from test_session_api import dispatch

from duckterm import memory_view
from duckterm.memory_sources import directory

__all__ = ["memory_rig"]


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
    monkeypatch.setattr("duckterm.memory_spans.MAX_RECORDS", 4)
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
        lambda _: SimpleNamespace(text="", failure_reason="provider_timeout"),
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

    def changed(*args):
        value = original(*args)
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
