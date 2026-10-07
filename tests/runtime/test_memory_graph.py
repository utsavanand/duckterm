"""Real canonical stores, exact-version retrieval and constrained graph writes."""

import asyncio
import io
import json
import sqlite3
import threading
from copy import deepcopy

import pytest
from test_maintained_progress import append, provider
from test_memory import memory_rig, transcript
from test_session_api import dispatch

from duckterm import memory_index, memory_records, memory_sources, session_client
from duckterm.cli import build_parser
from duckterm.core.session_api import APIError
from duckterm.memory import Memory
from duckterm.persistence.artifacts import registration

__all__ = ["memory_rig"]


def sources(server):
    return asyncio.run(server.memory.operate("agent", "sources", {}))["sources"]


def ref(source, record="0"):
    return {"source": source["id"] + ":" + source["version"], "record": record}


def read(server, endpoint):
    return asyncio.run(
        server.memory.operate("agent", "read", {k: [v] for k, v in endpoint.items()})
    )


def related(server, endpoint, **params):
    return asyncio.run(
        server.memory.operate(
            "agent", "related", {k: [str(v)] for k, v in {**endpoint, **params}.items()}
        )
    )


def write(server, headers, body):
    return dispatch(
        server, "POST", "/api/v1/session/memory/link", headers, json.dumps(body).encode()
    )


def register(server, headers, path, text):
    path.write_text(text)
    status, result = dispatch(
        server,
        "POST",
        "/api/v1/session/artifacts",
        headers,
        json.dumps(registration(path)).encode(),
    )
    assert status == 200, result
    return result["artifact"]


def sample(memory_rig):
    server, headers, _, tmp = memory_rig
    native = transcript(tmp, "agent-claude", "Owner: keep glacier decisions.")
    status, task = dispatch(
        server, "POST", "/api/v1/session/tasks", headers, b'{"title":"Produce glacier report"}'
    )
    assert status == 200, task
    artifact = register(server, headers, tmp / "report.md", "Glacier original artifact")
    catalog = sources(server)
    body = {
        "from": ref(next(s for s in catalog if s["kind"] == "task")),
        "relation": "produced",
        "to": ref(next(s for s in catalog if s["kind"] == "artifact")),
        "evidence": [ref(next(s for s in catalog if s["kind"] == "conversation"))],
        "request_key": "report-link",
    }
    return body, native, artifact


def test_revision_claims_checkpoint_and_structural_links_use_same_read_tools(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner glacier constraints")
    provider(monkeypatch)
    revision = asyncio.run(server.progress_coordinator.refresh("agent"))
    cp = asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    catalog = sources(server)
    summary = next(s for s in catalog if s["kind"] == "revision")
    checkpoint = next(s for s in catalog if s["kind"] == "checkpoint")
    assert summary["revision_id"] == revision["id"]
    assert checkpoint["checkpoint_id"] == cp["id"]
    assert "derived_claim" in read(server, ref(summary, "constraints:0"))["text"]
    assert revision["id"] in read(server, ref(checkpoint))["text"]
    graph = related(server, ref(summary, "constraints:0"))
    native_id = next(s["id"] for s in catalog if s["kind"] == "conversation")
    evidence = next(
        r
        for r in graph["relations"]
        if r["relation"] == "supports" and r["from"]["source"].startswith(native_id + ":")
    )
    assert evidence["provenance"]["kind"] == "summary_pipeline"
    assert "glacier" in read(server, evidence["from"])["text"]
    assert any(
        r["relation"] == "references_revision"
        for r in related(server, ref(checkpoint))["relations"]
    )
    # Derived projections never become original input during the next refresh.
    capture = asyncio.run(server.progress_coordinator.capture("agent"))
    from duckterm.memory_continuity import capture as maintained_capture

    original = asyncio.run(maintained_capture(server, "agent", capture))
    assert not {"revision", "checkpoint"} & {s["kind"] for s in original["memory_sources"]}


def test_exact_cited_artifact_and_native_versions_survive_update_reopen_and_are_revocable(
    memory_rig,
):
    server, headers, _, tmp = memory_rig
    body, native, artifact = sample(memory_rig)
    status, saved = write(server, headers, body)
    assert status == 200, saved
    assert saved["relation"]["provenance"]["authority"] == "none"
    assert write(server, headers, body)[1]["replayed"]
    register(server, headers, tmp / "report.md", "Glacier replacement artifact")
    native.write_text(native.read_text().replace("keep", "drop"))
    server.memory = Memory(server)
    assert "original" in read(server, body["to"])["text"]
    assert "keep" in read(server, body["evidence"][0])["text"]
    matches = asyncio.run(
        server.memory.operate("agent", "search", {"q": ["Glacier"], "limit": ["50"]})
    )["results"]
    assert any(r["source"] == body["to"]["source"] for r in matches)
    assert any("replacement" in r["excerpt"] for r in matches)
    server.history.artifacts.remove("agent", artifact["id"])
    with pytest.raises(APIError):
        read(server, body["to"])
    assert not any(
        r["id"] == saved["relation"]["id"] for r in related(server, body["from"])["relations"]
    )
    matches = asyncio.run(server.memory.operate("agent", "search", {"q": ["artifact"]}))["results"]
    assert not any("Glacier original artifact" in r["excerpt"] for r in matches)


def test_removing_last_canonical_reference_revokes_orphan_snapshot_and_cached_search(memory_rig):
    server, headers, _, tmp = memory_rig
    body, _, _ = sample(memory_rig)
    saved = write(server, headers, body)[1]["relation"]
    register(server, headers, tmp / "report.md", "Replacement text")
    assert "original" in read(server, body["to"])["text"]
    with server.history._conn:
        server.history._conn.execute(
            "DELETE FROM digest_items WHERE id=? AND bucket=?",
            (saved["id"], memory_records.LINK_BUCKET),
        )
    with pytest.raises(APIError):
        read(server, body["to"])
    assert list(memory_sources.directory("agent").glob("record-*.json"))
    assert not asyncio.run(server.memory.operate("agent", "search", {"q": ["original artifact"]}))[
        "results"
    ]


@pytest.mark.parametrize(
    "mutation", ["owner", "structural", "bad_type", "no_evidence", "wrong_record", "foreign_source"]
)
def test_link_rejects_forged_authority_invalid_types_and_inaccessible_evidence(
    memory_rig, mutation
):
    server, headers, _, tmp = memory_rig
    body, _, _ = sample(memory_rig)
    if mutation == "owner":
        body["provenance"] = {"author": "owner"}
    elif mutation == "structural":
        body["relation"] = "processed"
    elif mutation == "bad_type":
        body["from"] = body["to"]
    elif mutation == "no_evidence":
        body["evidence"] = []
    elif mutation == "wrong_record":
        body["evidence"][0]["record"] = "missing"
    else:
        transcript(tmp, "peer-claude", "Peer private conversation")
        peer = asyncio.run(server.memory.operate("peer", "sources", {}))["sources"]
        body["evidence"] = [ref(next(s for s in peer if s["kind"] == "conversation"))]
    status, _ = write(server, headers, body)
    assert status in (400, 404, 409)
    assert not server.history._conn.execute(
        "SELECT 1 FROM digest_items WHERE bucket=?", (memory_records.LINK_BUCKET,)
    ).fetchone()


def test_idempotency_conflict_and_simultaneous_retries_make_one_link(memory_rig):
    server, headers, _, _ = memory_rig
    body, _, _ = sample(memory_rig)

    async def run():
        return await asyncio.gather(
            *(server.memory.link("agent", deepcopy(body), headers) for _ in range(2))
        )

    results = asyncio.run(run())
    assert sorted(r["replayed"] for r in results) == [False, True]
    altered = {**body, "evidence": [body["from"]]}
    assert write(server, headers, altered)[0] == 409
    assert (
        server.history._conn.execute(
            "SELECT count(*) FROM digest_items WHERE bucket=?", (memory_records.LINK_BUCKET,)
        ).fetchone()[0]
        == 1
    )


def test_pagination_has_no_duplicates_and_rejects_changed_graph(memory_rig):
    server, headers, _, _ = memory_rig
    body, native, _ = sample(memory_rig)
    for index in range(3):
        assert write(server, headers, {**body, "request_key": f"link-{index}"})[0] == 200
    all_relations = related(server, body["from"])["relations"]
    page = related(server, body["from"], limit=1)
    ids = [r["id"] for r in page["relations"]]
    first_cursor = page["next_cursor"]
    while page["next_cursor"]:
        page = related(server, body["from"], limit=1, cursor=page["next_cursor"])
        ids.extend(r["id"] for r in page["relations"])
    assert ids == [r["id"] for r in all_relations] and len(set(ids)) == len(ids)
    append(native, "New owner instruction")
    with pytest.raises(APIError, match="changed"):
        related(server, body["from"], limit=1, cursor=first_cursor)
    with pytest.raises(APIError):
        related(server, body["from"], cursor="not-a-cursor")


@pytest.mark.parametrize("revoke", ["scope", "token"])
def test_access_revoked_during_snapshot_write_prevents_link_commit(memory_rig, monkeypatch, revoke):
    server, headers, _, _ = memory_rig
    body, _, _ = sample(memory_rig)
    from duckterm import memory_versions

    original = memory_versions.retain
    entered, released = threading.Event(), threading.Event()

    def paused(*args):
        result = original(*args)
        entered.set()
        assert released.wait(3)
        return result

    monkeypatch.setattr(memory_versions, "retain", paused)

    async def run():
        task = asyncio.create_task(server.memory.link("agent", body, headers))
        assert await asyncio.to_thread(entered.wait, 3)
        if revoke == "scope":
            server.history.set_meta("agent", group="Elsewhere")
        else:
            with server.history._conn:
                server.history._conn.execute(
                    "UPDATE session_api_members SET token_hash='revoked' WHERE session_key='agent'"
                )
        released.set()
        with pytest.raises(APIError):
            await task

    asyncio.run(run())
    assert not server.history._conn.execute(
        "SELECT 1 FROM digest_items WHERE bucket=?", (memory_records.LINK_BUCKET,)
    ).fetchone()


def test_cli_sends_file_as_json_and_related_pagination_without_owner_credentials(
    tmp_path, monkeypatch
):
    body = {"from": {"source": "a" * 32 + ":" + "b" * 64, "record": "0"}, "relation": "produced"}
    path = tmp_path / "link.json"
    path.write_text(json.dumps(body))
    requests = []

    def http(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b"{}")

    monkeypatch.setattr(
        session_client, "client_credentials", lambda: ("http://localhost", "own-session")
    )
    monkeypatch.setattr(session_client.urllib.request, "urlopen", http)
    assert (
        session_client.main(build_parser().parse_args(["memory", "link", "--file", str(path)])) == 0
    )
    assert requests[0].method == "POST" and json.loads(requests[0].data) == body
    assert (
        session_client.main(
            build_parser().parse_args(
                [
                    "memory",
                    "related",
                    body["from"]["source"],
                    "--record",
                    "0",
                    "--limit",
                    "3",
                    "--cursor",
                    "next",
                ]
            )
        )
        == 0
    )
    assert requests[1].method == "GET" and "cursor=next" in requests[1].full_url


def test_disposable_single_version_index_upgrades_without_canonical_schema_change(tmp_path):
    path = tmp_path / "index.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE versions (id TEXT PRIMARY KEY, version TEXT NOT NULL)")
        conn.execute(
            "CREATE VIRTUAL TABLE chunks USING fts5(source_id UNINDEXED, version UNINDEXED, "
            "record_id UNINDEXED, offset UNINDEXED, title, body)"
        )
    source = {
        "id": "one",
        "kind": "artifact",
        "title": "Artifact",
        "records": [{"id": "0", "text": "glacier"}],
    }
    found = memory_index.query(
        path, [{**source, "version": "old"}, {**source, "version": "new"}], "glacier", 20
    )
    assert {r["version"] for r in found} == {"old", "new"}


def test_support_and_supersession_keep_claim_versions_and_never_promote_authority(
    memory_rig, monkeypatch
):
    server, headers, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Earlier owner constraint")
    provider(monkeypatch)
    first = asyncio.run(server.progress_coordinator.refresh("agent"))
    append(path, "Owner correction supersedes the earlier constraint")
    second = asyncio.run(server.progress_coordinator.refresh("agent"))
    catalog = sources(server)
    older = next(s for s in catalog if s.get("revision_id") == first["id"])
    newer = next(s for s in catalog if s.get("revision_id") == second["id"])
    native = next(s for s in catalog if s["kind"] == "conversation" and s.get("current"))
    evidence = ref(native, "1")
    body = {
        "from": ref(newer, "constraints:0"),
        "to": ref(older, "constraints:0"),
        "relation": "supersedes",
        "evidence": [evidence],
        "request_key": "correction",
    }
    status, saved = write(server, headers, body)
    assert status == 200, saved
    assert saved["relation"]["provenance"]["kind"] == "agent_assertion"
    assert "derived_claim" in read(server, ref(older, "constraints:0"))["text"]
    assert (
        write(
            server,
            headers,
            {**body, "from": body["to"], "to": body["from"], "request_key": "reverse"},
        )[0]
        == 400
    )
    support = {
        **body,
        "from": evidence,
        "to": ref(newer, "constraints:0"),
        "relation": "supports",
        "request_key": "support",
    }
    assert write(server, headers, support)[0] == 200
    assert (
        write(
            server, headers, {**support, "evidence": [body["from"]], "request_key": "derived-proof"}
        )[0]
        == 400
    )
    assert any(r["id"] == saved["relation"]["id"] for r in related(server, body["to"])["relations"])


def test_summary_cited_artifact_version_is_readable_then_removed_claims_leave_index(
    memory_rig, monkeypatch
):
    server, headers, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner requires preserving archive")
    artifact = register(server, headers, tmp / "report.md", "Glacier ancient cited artifact")
    calls = provider(monkeypatch)
    revision = asyncio.run(server.progress_coordinator.refresh("agent"))
    assert len(calls) == 2
    catalog = sources(server)
    summary = next(s for s in catalog if s.get("revision_id") == revision["id"])
    old_artifact = ref(next(s for s in catalog if s["kind"] == "artifact"))
    assert "current owner constraints" in read(server, ref(summary, "constraints:0"))["text"]
    asyncio.run(server.memory.operate("agent", "search", {"q": ["current owner constraints"]}))
    register(server, headers, tmp / "report.md", "Replacement artifact")
    assert "ancient" in read(server, old_artifact)["text"]
    server.history.artifacts.remove("agent", artifact["id"])
    with pytest.raises(APIError):
        read(server, ref(summary, "constraints:0"))
    with pytest.raises(APIError):
        read(server, ref(summary, "summary"))
    found = asyncio.run(server.memory.operate("agent", "search", {"q": ["constraints"]}))["results"]
    assert not any(
        r["source_id"] == summary["id"] and r["record_id"] == "constraints:0" for r in found
    )


def test_link_endpoint_changed_during_retention_cannot_commit(memory_rig, monkeypatch):
    server, headers, _, tmp = memory_rig
    body, _, artifact = sample(memory_rig)
    from duckterm import memory_versions

    original = memory_versions.retain
    entered, released = threading.Event(), threading.Event()

    def paused(*args):
        result = original(*args)
        entered.set()
        assert released.wait(3)
        return result

    monkeypatch.setattr(memory_versions, "retain", paused)

    async def run():
        task = asyncio.create_task(server.memory.link("agent", body, headers))
        assert await asyncio.to_thread(entered.wait, 3)
        server.history.artifacts.remove("agent", artifact["id"])
        released.set()
        with pytest.raises(APIError):
            await task

    asyncio.run(run())
    assert not server.history._conn.execute(
        "SELECT 1 FROM digest_items WHERE bucket=?", (memory_records.LINK_BUCKET,)
    ).fetchone()


def test_corrupt_retained_citation_never_returns_current_text_in_its_place(memory_rig):
    server, headers, _, tmp = memory_rig
    body, _, _ = sample(memory_rig)
    saved = write(server, headers, body)[1]["relation"]
    row = server.history._conn.execute(
        "SELECT text FROM digest_items WHERE id=?", (saved["id"],)
    ).fetchone()
    value = json.loads(row[0])
    retained = next(r for r in value["retained_sources"] if r["kind"] == "artifact")
    register(server, headers, tmp / "report.md", "Replacement artifact")
    path = memory_sources.directory("agent") / ("record-" + retained["text_snapshot"] + ".json")
    path.write_text("broken")
    with pytest.raises(APIError):
        read(server, body["to"])
    assert not any(r["id"] == saved["id"] for r in related(server, body["from"])["relations"])


def test_sources_include_markers_and_claims_after_service_restart(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Durable owner context")
    provider(monkeypatch)
    asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    before = sources(server)
    server.memory = Memory(server)
    assert sources(server) == before
    assert {"revision", "checkpoint", "conversation"} <= {s["kind"] for s in before}


def test_related_computation_keeps_event_loop_responsive_and_rechecks_scope(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Owner glacier constraint")
    native = next(s for s in sources(server) if s["kind"] == "conversation")
    from duckterm import memory_graph

    original = memory_graph.related
    entered, released = threading.Event(), threading.Event()

    def slow(*args):
        entered.set()
        assert released.wait(3)
        return original(*args)

    monkeypatch.setattr(memory_graph, "related", slow)

    async def run():
        task = asyncio.create_task(
            server.memory.operate("agent", "related", {"source": [ref(native)["source"]]})
        )
        assert await asyncio.to_thread(entered.wait, 3)
        # This executes while graph traversal is still running off the event loop.
        server.history.set_meta("agent", group="Elsewhere")
        released.set()
        with pytest.raises(APIError, match="changed"):
            await task

    asyncio.run(run())


def test_link_rechecks_catalog_after_acquiring_database_write_boundary(memory_rig, monkeypatch):
    server, headers, _, _ = memory_rig
    body, _, _ = sample(memory_rig)
    from contextlib import contextmanager

    from duckterm import memory

    original = memory.transaction

    @contextmanager
    def changed_before_lock(conn):
        server.history.set_meta("agent", notes="Owner changed required work before commit")
        with original(conn):
            yield

    monkeypatch.setattr(memory, "transaction", changed_before_lock)
    assert write(server, headers, body)[0] == 409
    assert not server.history._conn.execute(
        "SELECT 1 FROM digest_items WHERE bucket=?", (memory_records.LINK_BUCKET,)
    ).fetchone()
