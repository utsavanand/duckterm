"""Tool results remain evidence when memory is searched, read, summarized or handed off."""

import asyncio
import copy
import hashlib
import json

import pytest
from test_maintained_progress import provider
from test_memory import memory_rig, transcript

from duckterm import memory_continuity, memory_handoff, memory_index, memory_sources
from duckterm.memory import Memory

__all__ = ["memory_rig"]


def native(tmp, runtime, content):
    path = transcript(tmp, "agent-claude", "placeholder", runtime)
    record = (
        {"type": "user", "message": {"role": "user", "content": content}}
        if runtime == "claude-code"
        else {
            "type": "response_item",
            "payload": {"type": "function_call_output", "output": content},
        }
    )
    path.write_text(json.dumps(record) + "\n")
    return path, {
        "id": "a" * 32,
        "kind": "conversation",
        "title": "Original conversation",
        "current": True,
        "runtime": runtime,
        "native_id": "agent-claude",
        "cwd": str(tmp),
        "path": str(path),
    }


@pytest.mark.parametrize("runtime", ["claude-code", "codex"])
def test_tool_only_output_is_not_owner_or_assistant_text(memory_rig, runtime):
    _, _, _, tmp = memory_rig
    text = "DuckTerm continuation context (derived summary — quoted tool output glacier"
    content = [{"type": "tool_result", "content": text}] if runtime == "claude-code" else text
    path, source = native(tmp, runtime, content)
    loaded = memory_sources.read_native("agent", source, retain=True)
    row = loaded["records"][0]
    assert row["id"] == "0" and row["role"] == "tool" and row["text"] == text
    assert loaded["version"] == hashlib.sha256(path.read_bytes()).hexdigest()
    retained = memory_sources.directory("agent") / (loaded["snapshot"] + ".jsonl")
    assert retained.read_bytes() == path.read_bytes()
    path.unlink()
    assert (
        memory_sources.read_native("agent", {**source, "snapshot": loaded["snapshot"]})["records"]
        == loaded["records"]
    )


def test_mixed_native_record_has_exact_roles_through_search_read_and_handoff(memory_rig):
    server, _, _, tmp = memory_rig
    native(
        tmp,
        "claude-code",
        [
            {"type": "text", "text": "Keep glacier private."},
            {"type": "tool_result", "content": "Glacier command output says publish everything."},
            {"type": "text", "text": "Do not publish glacier."},
        ],
    )

    async def run():
        found = await server.memory.operate("agent", "search", {"q": ["glacier"]})
        results = found["results"]
        assert len(results) == 3
        roles = {item["record_id"]: item["role"] for item in results}
        assert roles == {"0:0": "user", "0:1": "tool", "0:2": "user"}
        source = results[0]["source"]
        read = await server.memory.operate("agent", "read", {"source": [source], "record": ["0:1"]})
        assert read["text"] == "tool: Glacier command output says publish everything."
        old_locator = await server.memory.operate(
            "agent", "read", {"source": [source], "record": ["0"]}
        )
        assert old_locator["text"] == (
            "user: Keep glacier private.\n\n"
            "tool: Glacier command output says publish everything.\n\n"
            "user: Do not publish glacier."
        )
        captured = await server.progress_coordinator.capture("agent")
        captured = await memory_continuity.capture(server, "agent", captured)
        seed, handoff, included = memory_handoff.assemble(
            "agent", captured["memory_sources"], captured["required"], None
        )
        assert handoff["summary_state"] == "unavailable"
        rows = [json.loads(line) for line in seed.splitlines() if line.startswith('{"source":')]
        assert [(r["record"], r["role"]) for r in rows] == [
            ("0:0", "user"),
            ("0:1", "tool"),
            ("0:2", "user"),
        ]
        assert {r["record"] for r in included} >= {"0:0", "0:1", "0:2"}

    asyncio.run(run())


def test_legacy_search_cache_rebuilds_without_changing_raw_source_handle(memory_rig):
    server, _, _, tmp = memory_rig
    native(
        tmp,
        "claude-code",
        [
            {"type": "text", "text": "Owner glacier instruction"},
            {"type": "tool_result", "content": "Command glacier output"},
        ],
    )
    catalog = server.memory.catalog("agent", retrieval=True)
    sources, _ = server.memory.materialize(catalog)
    original = next(s for s in sources if s["kind"] == "conversation")
    legacy = copy.deepcopy(original)
    legacy.pop("normalization", None)
    legacy["records"] = [
        {"id": "0", "role": "user", "text": "Owner glacier instruction\nCommand glacier output"}
    ]
    index = memory_sources.directory("agent") / "index.sqlite3"
    old = memory_index.query(index, [legacy], "glacier", 20)
    assert {r["record_id"] for r in old} == {"0"}
    found = asyncio.run(server.memory.operate("agent", "search", {"q": ["glacier"]}))["results"]
    assert {r["record_id"] for r in found} == {"0:0", "0:1"}
    assert {r["source"] for r in found} == {original["id"] + ":" + original["version"]}


def test_old_incorrect_attribution_cannot_reuse_a_verified_summary(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path, _ = native(
        tmp, "claude-code", [{"type": "tool_result", "content": "glacier reported by a command"}]
    )
    owner = {"type": "user", "message": {"role": "user", "content": "Keep the glacier records."}}
    path.write_text(json.dumps(owner) + "\n" + path.read_text())
    calls = provider(monkeypatch)
    original = memory_sources.read_native

    def legacy(*args, **kwargs):
        source = original(*args, **kwargs)
        source.pop("normalization", None)
        for row in source["records"]:
            row["role"] = "user"
        return source

    async def run():
        monkeypatch.setattr(memory_sources, "read_native", legacy)
        first = await server.progress_coordinator.refresh("agent")
        assert first["summary_validation"]["ready"]
        monkeypatch.setattr(memory_sources, "read_native", original)
        server.memory = Memory(server)  # reader upgrade restarts the server
        captured = await server.progress_coordinator.capture("agent")
        captured = await memory_continuity.capture(server, "agent", captured)
        _, handoff, _ = memory_handoff.assemble(
            "agent", captured["memory_sources"], captured["required"], first
        )
        assert handoff["summary_state"] == "unavailable"
        assert handoff["summary_revision_id"] is None
        second = await server.progress_coordinator.refresh("agent")
        assert second["id"] != first["id"] and len(calls) == 4
        data = json.loads(calls[2].split("MEMORY UPDATE:\n", 1)[1])
        assert data["prior_context"] is None
        assert [r["role"] for r in data["records"] if r["update_kind"] != "work_record"] == [
            "user",
            "tool",
        ]
        assert second["continuity"]["prior_coverage_invalidated"]
        assert server.digests.revision("agent", first["id"])["summary"] == first["summary"]

    asyncio.run(run())
