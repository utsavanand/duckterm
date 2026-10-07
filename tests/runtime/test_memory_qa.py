"""Independent retrieval negatives through session credentials and native files."""

import asyncio
import json

import pytest
from test_session_api import dispatch
from tests.runtime.test_memory import memory_rig, transcript

from duckterm import memory_sources

__all__ = ["memory_rig"]


def test_current_retained_conversation_remains_discoverable_after_provider_cleanup(memory_rig):
    server, headers, _, tmp = memory_rig
    native = transcript(tmp, "agent-claude", "glacier retained owner decision")
    asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "retain"))
    native.unlink()
    status, body = dispatch(server, "GET", "/api/v1/session/memory/search?q=glacier", headers)
    assert status == 200
    assert any("retained owner decision" in r["excerpt"] for r in body["results"])


@pytest.mark.parametrize("runtime", ["claude-code", "codex"])
def test_unhashable_native_block_type_is_unavailable_not_uncaught(memory_rig, runtime):
    server, headers, _, tmp = memory_rig
    native_id = "agent-claude" if runtime == "claude-code" else "agent-codex"
    if runtime == "codex":
        server.history.set_harness_identity(
            "agent",
            "codex",
            None,
            None,
            control={
                "native_binding": {"runtime": "codex", "native_id": native_id, "generation": "b"}
            },
        )
    path = transcript(tmp, native_id, "glacier", runtime)
    record = json.loads(path.read_text())
    payload = record["message" if runtime == "claude-code" else "payload"]
    payload["content"] = [{"type": [], "text": "glacier"}]
    path.write_text(json.dumps(record) + "\n")
    status, body = dispatch(server, "GET", "/api/v1/session/memory/search?q=glacier", headers)
    assert status == 200
    assert body["coverage"]["complete"] is False
    assert body["coverage"]["unavailable"]
    assert body["results"] == []


def test_native_symlink_does_not_expose_target_text(memory_rig):
    server, headers, _, tmp = memory_rig
    native = transcript(tmp, "agent-claude", "glacier")
    target = tmp / "private.txt"
    target.write_text(native.read_text())
    native.unlink()
    native.symlink_to(target)
    status, body = dispatch(server, "GET", "/api/v1/session/memory/search?q=glacier", headers)
    assert status == 200
    assert not body["coverage"]["complete"]
    assert body["results"] == []


def test_native_line_over_bound_is_unavailable_and_leaves_no_capture(memory_rig, monkeypatch):
    server, headers, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "glacier " * 40)
    monkeypatch.setattr(memory_sources, "MAX_LINE_BYTES", 100)
    status, body = dispatch(server, "GET", "/api/v1/session/memory/search?q=glacier", headers)
    assert status == 200
    assert not body["coverage"]["complete"]
    assert body["results"] == []
    assert not list(memory_sources.directory("agent").glob(".capture-*"))


def test_credential_revocation_during_worker_read_blocks_return_without_stalling_loop(
    memory_rig, monkeypatch
):
    import threading

    from duckterm.core.session_api import APIError

    server, headers, _, tmp = memory_rig
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
        task = asyncio.create_task(
            server.memory.session_request("GET", "/api/v1/session/memory/search?q=glacier", headers)
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            # The owner thread continues while native I/O is paused, then revokes
            # the credential independently of the catalog's current scope.
            with server.history._conn:
                server.history._conn.execute(
                    "UPDATE session_api_members SET token_hash='revoked-test-hash' "
                    "WHERE session_key='agent'"
                )
            released.set()
            with pytest.raises(APIError):
                await task
        finally:
            released.set()
            if not task.done():
                await task

    asyncio.run(run())
