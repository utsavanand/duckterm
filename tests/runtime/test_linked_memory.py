"""Caller credentials can follow authorized links without copying or rewriting memory."""

import asyncio
import json
import urllib.parse

import pytest
from test_maintained_progress import append, provider
from test_memory import memory_rig, transcript
from test_session_api import dispatch

from duckterm.memory import Memory
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

__all__ = ["memory_rig"]


def call(server, headers, action, **params):
    return dispatch(
        server,
        "GET",
        "/api/v1/session/memory/" + action + "?" + urllib.parse.urlencode(params),
        headers,
    )


def child_of(server, child="agent", parent="peer"):
    server.history._conn.execute(
        "UPDATE sessions SET parent_session_key=? WHERE session_key=?", (parent, child)
    )
    server.history._conn.commit()


def test_parent_reads_every_child_summary_and_evidence_after_close_and_reopen(
    memory_rig, monkeypatch
):
    server, _, parent_headers, tmp = memory_rig
    child_of(server)
    path = transcript(tmp, "agent-claude", "Keep the glacier original decision.")
    provider(monkeypatch)
    for n in range(3):
        if n:
            append(path, f"Glacier decision {n}: preserve source evidence.")
        result = asyncio.run(server.progress_coordinator.refresh("agent"))
        assert result["id"]
    server.history._conn.execute(
        "UPDATE sessions SET state='merged',merged_into='peer' WHERE session_key='agent'"
    )
    server.history._conn.commit()
    server.memory = Memory(server)
    code, links = call(server, parent_headers, "sessions")
    assert code == 200 and links["sessions"][0]["session"] == "agent"
    assert links["sessions"][0]["state"] == "merged"
    revisions, cursor = [], None
    while True:
        code, page = call(
            server,
            parent_headers,
            "sources",
            session="agent",
            kind="revision",
            limit=1,
            **({"cursor": cursor} if cursor else {}),
        )
        assert code == 200, page
        revisions.extend(page["sources"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len({r["revision_id"] for r in revisions}) == 3
    # Native sources can retain several versions under the same source id.
    # A page boundary must not skip the remaining versions of that id.
    all_sources = call(server, parent_headers, "sources", session="agent", limit=100)[1]["sources"]
    seen, cursor = [], None
    while True:
        page = call(
            server,
            parent_headers,
            "sources",
            session="agent",
            limit=1,
            **({"cursor": cursor} if cursor else {}),
        )[1]
        seen.extend((s["id"], s["version"]) for s in page["sources"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len({s["id"] for s in all_sources}) < len(all_sources)
    assert next(s for s in all_sources if s["kind"] == "conversation")["current"]
    assert seen == [(s["id"], s["version"]) for s in all_sources]
    for row in revisions:
        source = row["id"] + ":" + row["version"]
        code, read = call(
            server, parent_headers, "read", session="agent", source=source, record="summary"
        )
        assert code == 200 and read["text"]
        assert read["coverage"]["scope"] == "linked_session"
        code, graph = call(server, parent_headers, "related", session="agent", source=source)
        assert code == 200
        evidence = next(e for e in graph["relations"] if e["relation"] == "processed")
        code, original = call(server, parent_headers, "read", session="agent", **evidence["to"])
        assert code == 200 and "glacier" in original["text"].lower()
    # A separate connection and server have none of the first reader's caches.
    path.unlink()  # Original provider cleanup must not erase retained evidence.
    reopened_history = HistoryStore(tmp / "db.sqlite")
    try:
        reopened = Server(history=reopened_history)
        code, page = call(reopened, parent_headers, "sources", session="agent", kind="revision")
        assert code == 200 and len(page["sources"]) == 3
        assert call(reopened, parent_headers, "read", session="agent", **evidence["to"])[0] == 200
    finally:
        reopened_history.close()


def test_linked_session_listing_pages_and_archived_source_stays_readable(memory_rig):
    server, _, parent_headers, _ = memory_rig
    child_of(server)
    server.history._conn.execute("UPDATE sessions SET state='archived' WHERE session_key='agent'")
    server.history._conn.commit()
    code, page = call(server, parent_headers, "sessions", limit=1)
    assert code == 200 and page["sessions"][0]["state"] == "archived"
    assert page["next_cursor"] is None
    assert call(server, parent_headers, "sources", session="agent")[0] == 200
    assert call(server, parent_headers, "sessions", cursor="agent")[1]["sessions"] == []


def test_unrelated_peer_and_child_cannot_read_parent_or_write(memory_rig):
    server, child_headers, parent_headers, _ = memory_rig
    assert call(server, parent_headers, "sources", session="agent")[0] == 403
    child_of(server)
    assert call(server, parent_headers, "sources", session="agent")[0] == 200
    assert call(server, child_headers, "sources", session="peer")[0] == 403
    assert (
        dispatch(
            server, "POST", "/api/v1/session/memory/link?session=agent", parent_headers, b"{}"
        )[0]
        == 400
    )
    assert call(server, parent_headers, "sources", session="invented")[0] == 403


def test_link_revoked_during_read_does_not_return_content(memory_rig, monkeypatch):
    server, _, parent_headers, tmp = memory_rig
    child_of(server)
    transcript(tmp, "agent-claude", "Private source decision")
    original = server.memory.operate

    async def change(*args, **kwargs):
        result = await original(*args, **kwargs)
        server.history._conn.execute(
            "UPDATE sessions SET parent_session_key=NULL WHERE session_key='agent'"
        )
        server.history._conn.commit()
        return result

    monkeypatch.setattr(server.memory, "operate", change)
    code, body = call(server, parent_headers, "search", session="agent", q="Private")
    assert code == 403 and "Private source decision" not in json.dumps(body)


@pytest.mark.parametrize("changed", ["root", "folder"])
def test_fork_link_does_not_cross_changed_sharing_scope(memory_rig, changed):
    server, _, parent_headers, _ = memory_rig
    child_of(server)
    server.history._conn.execute(
        f"UPDATE session_api_members SET {changed}='other' WHERE session_key='agent'"
    )
    server.history._conn.commit()
    assert call(server, parent_headers, "sources", session="agent")[0] == 403


def test_cli_can_select_linked_sources_without_a_write_selector():
    from duckterm.cli import build_parser

    parser = build_parser()
    assert (
        parser.parse_args(["memory", "sources", "--session", "child", "--kind", "revision"]).session
        == "child"
    )
    assert parser.parse_args(["memory", "sessions", "--limit", "1"]).limit == 1
    with pytest.raises(SystemExit):
        parser.parse_args(["memory", "claim", "--file", "x", "--session", "child"])


def test_merge_receipt_points_to_reviewed_revision_after_newer_summary(memory_rig, monkeypatch):
    from duckterm import agent_merges

    server, _, parent_headers, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Keep the glacier original decision.")
    provider(monkeypatch)
    revision = asyncio.run(server.progress_coordinator.refresh("agent"))["id"]
    review = asyncio.run(agent_merges.preview(server, "agent", "peer"))
    draft = {
        "target": "peer",
        "revision": review["revision"],
        "context": review["context"],
        "notes": None,
        "code": False,
        "requestKey": "saved-revision",
        "memory": True,
    }
    receipt = asyncio.run(agent_merges.send(server, "agent", draft))
    append(path, "A newer glacier decision: preserve source evidence.")
    newer = asyncio.run(server.progress_coordinator.refresh("agent"))["id"]
    assert newer != revision
    retry = asyncio.run(agent_merges.send(server, "agent", draft))
    assert receipt["summaryRevision"] == retry["summaryRevision"] == revision
    assert revision in retry["packet"]
    code, page = call(server, parent_headers, "sources", session="agent", kind="revision")
    assert code == 200 and {s["revision_id"] for s in page["sources"]} == {revision, newer}
