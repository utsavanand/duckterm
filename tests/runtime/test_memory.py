"""Real native fixtures prove cross-harness lookup, retention and session isolation."""

import asyncio
import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from test_session_api import dispatch, enroll

from duckterm.core.session_api import APIError
from duckterm.memory_sources import directory, read_native
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import project_slug
from duckterm.server import Server


@pytest.fixture
def memory_rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    monkeypatch.setenv("DUCKTERM_SUMMARIZER", "off")
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_CMD", raising=False)
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_URL", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=history)
    for key in ("agent", "peer"):
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "runtime": "claude-code",
                "session_id": key + "-claude",
                "cwd": str(tmp_path),
                "test": True,
            }
        )
        history.set_meta(key, group="Tests", name=key)
    headers = enroll(history, "agent", "Tests")
    peer_headers = enroll(history, "peer", "Tests")
    yield server, headers, peer_headers, tmp_path
    history.purge_test_sessions()
    history.close()


def transcript(tmp, native_id, text, runtime="claude-code"):
    if runtime == "claude-code":
        path = tmp / ".claude" / "projects" / project_slug(tmp) / (native_id + ".jsonl")
        record = {"type": "user", "message": {"role": "user", "content": text}}
    else:
        path = tmp / ".codex" / "sessions" / ("rollout-now-" + native_id + ".jsonl")
        record = {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": text},
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record) + "\n")
    return path


async def lookup(server, text):
    return await server.memory.operate("agent", "search", {"q": [text]})


def test_three_generations_and_original_text_survive_source_cleanup_and_reopen(memory_rig):
    server, _, _, tmp = memory_rig
    a = transcript(tmp, "agent-claude", "Owner constraint: keep the glacier archive in GCP.")

    async def run():
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert cp["record"]["memory_source"]["snapshot"]
        server.history.set_harness_identity(
            "agent",
            "codex",
            None,
            None,
            control={
                "native_binding": {
                    "runtime": "codex",
                    "native_id": "agent-codex",
                    "generation": "b",
                }
            },
        )
        b = transcript(tmp, "agent-codex", "Implemented the glacier archive with checks.", "codex")
        await server._create_checkpoint("agent", server.history.session("agent"), "before switch")
        server.history.set_harness_identity(
            "agent",
            "claude-code",
            None,
            None,
            control={
                "native_binding": {
                    "runtime": "claude-code",
                    "native_id": "agent-third",
                    "generation": "c",
                }
            },
        )
        transcript(tmp, "agent-third", "Continue the same work.")
        a.unlink()
        b.unlink()
        result = await lookup(server, "glacier")
        assert len(result["results"]) == 2
        original = next(r for r in result["results"] if "Owner constraint" in r["excerpt"])
        read = await server.memory.operate("agent", "read", {"source": [original["source"]]})
        assert "keep the glacier archive in GCP" in read["text"]
        assert server.history.session("agent")["name"] == "agent"
        return original["source"]

    handle = asyncio.run(run())
    # A new service reconstructs the chain from durable records, without the
    # previous process's in-memory cache or a single previous-conversation field.
    from duckterm.memory import Memory

    server.memory = Memory(server)
    read = asyncio.run(server.memory.operate("agent", "read", {"source": [handle]}))
    assert "Owner constraint" in read["text"]


def test_session_credential_cannot_read_peers_even_in_same_folder(memory_rig):
    server, headers, peer_headers, tmp = memory_rig
    transcript(tmp, "agent-claude", "Alpha-only specialterm")
    transcript(tmp, "peer-claude", "Beta-only specialterm")
    status, body = dispatch(server, "GET", "/api/v1/session/memory/search?q=specialterm", headers)
    assert (
        status == 200 and "Alpha-only" in json.dumps(body) and "Beta-only" not in json.dumps(body)
    )
    handle = body["results"][0]["source"]
    status, body = dispatch(
        server, "GET", "/api/v1/session/memory/read?source=" + handle, peer_headers
    )
    assert status == 409 and "Alpha-only" not in json.dumps(body)
    assert dispatch(server, "GET", "/api/v1/session/memory/sources", {})[0] == 401
    assert dispatch(server, "GET", "/api/v1/session/memory/sources?session=peer", headers)[0] == 400
    assert dispatch(server, "POST", "/api/v1/session/memory/search", headers)[0] == 405


def test_old_snapshot_handle_keeps_exact_version_then_checkpoint_removal_revokes_it(memory_rig):
    server, _, _, tmp = memory_rig
    native = transcript(tmp, "agent-claude", "Original glacier decision")

    async def run():
        cp = await server._create_checkpoint("agent", server.history.session("agent"), "first")
        first = (await lookup(server, "glacier"))["results"][0]["source"]
        native.write_text(native.read_text().replace("Original", "Revised!"))
        await server._create_checkpoint("agent", server.history.session("agent"), "second")
        old = await server.memory.operate("agent", "read", {"source": [first]})
        assert "Original" in old["text"] and "Revised!" not in old["text"]
        with server.history._conn:
            server.history._conn.execute("DELETE FROM checkpoints WHERE id=?", (cp["id"],))
        with pytest.raises(APIError, match="removed"):
            await server.memory.operate("agent", "read", {"source": [first]})

    asyncio.run(run())


def test_same_size_same_mtime_rewrite_invalidates_native_handle(memory_rig):
    server, _, _, tmp = memory_rig
    native = transcript(tmp, "agent-claude", "original glacier")
    first = asyncio.run(lookup(server, "glacier"))["results"][0]["source"]
    stat = native.stat()
    native.write_text(native.read_text().replace("original", "modified"))
    os.utime(native, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(APIError, match="changed"):
        asyncio.run(server.memory.operate("agent", "read", {"source": [first]}))


def test_bad_partial_record_is_unavailable_not_complete(memory_rig):
    server, _, _, tmp = memory_rig
    native = transcript(tmp, "agent-claude", "glacier")
    with native.open("a") as stream:
        stream.write('{"unfinished":')
    result = asyncio.run(lookup(server, "glacier"))
    assert not result["coverage"]["complete"] and result["results"] == []
    assert "unreadable" in result["coverage"]["unavailable"][0]["reason"]


def test_index_corruption_rebuilds_and_session_delete_removes_private_files(memory_rig):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "glacier")
    assert asyncio.run(lookup(server, "glacier"))["results"]
    folder = directory("agent")
    index = folder / "index.sqlite3"
    assert index.stat().st_mode & 0o777 == 0o600
    index.write_bytes(b"broken index")
    assert asyncio.run(lookup(server, "glacier"))["results"]
    server.history.delete_session("agent")
    assert not folder.exists()


def test_scope_change_during_worker_read_does_not_return_cached_private_text(
    memory_rig, monkeypatch
):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "glacier private")
    entered, released = threading.Event(), threading.Event()
    original = server.memory.materialize

    def paused(catalog):
        result = original(catalog)
        entered.set()
        assert released.wait(5)
        return result

    monkeypatch.setattr(server.memory, "materialize", paused)

    async def run():
        task = asyncio.create_task(lookup(server, "glacier"))
        assert await asyncio.to_thread(entered.wait, 5)
        server.history.set_meta("agent", group="Elsewhere")
        # Scope captures must see the folder as well as root; moved enrollment
        # cannot be reused to publish data captured before the move.
        released.set()
        with pytest.raises(APIError, match="changed"):
            await task

    asyncio.run(run())


def test_raw_retention_is_atomic_and_corruption_is_detected(memory_rig):
    _, _, _, tmp = memory_rig
    native = transcript(tmp, "agent-claude", "glacier")
    source = {
        "id": "source",
        "runtime": "claude-code",
        "native_id": "agent-claude",
        "cwd": str(tmp),
        "title": "Claude",
    }
    saved = read_native("agent", source, retain=True)
    retained = directory("agent") / (saved["snapshot"] + ".jsonl")
    assert retained.read_bytes() == native.read_bytes()
    assert retained.stat().st_mode & 0o777 == 0o600
    retained.write_text(retained.read_text().replace("glacier", "altered"))
    with pytest.raises(APIError, match="checksum"):
        read_native("agent", {**source, "snapshot": saved["snapshot"]})
    assert not list(directory("agent").glob(".capture-*"))


def test_queries_are_literal_and_read_pagination_is_complete(memory_rig):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "🦆 glacier " * 2000)
    result = asyncio.run(lookup(server, 'glacier" OR * NEAR('))
    handle = result["results"][0]["source"]

    async def read_all():
        offset, pieces = 0, []
        while True:
            page = await server.memory.operate(
                "agent", "read", {"source": [handle], "offset": [str(offset)], "limit": ["1700"]}
            )
            pieces.append(page["text"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        return "".join(pieces)

    assert asyncio.run(read_all()) == "user: " + "🦆 glacier " * 2000
    with pytest.raises(APIError):
        asyncio.run(server.memory.operate("agent", "read", {"source": ["../../private"]}))


def test_fts_available_in_stdlib():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE VIRTUAL TABLE words USING fts5(text)")
