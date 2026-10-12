"""Keep tool evidence intact while excluding its successful bodies from summary input."""

import asyncio
import base64
import json

import pytest
from tests.runtime.test_maintained_progress import provider
from tests.runtime.test_memory import memory_rig, transcript
from tests.runtime.test_memory_tool_provenance import native

from duckterm import memory_sources, memory_summary_input

__all__ = ["memory_rig"]


@pytest.mark.parametrize("runtime", ["claude-code", "codex"])
def test_native_tool_output_stays_readable_but_reference_only(memory_rig, runtime):
    _, _, _, tmp = memory_rig
    text = "SUCCESS TOOL BODY " * 2000
    content = [{"type": "tool_result", "content": text}] if runtime == "claude-code" else text
    _, source = native(tmp, runtime, content)
    loaded = memory_sources.read_native("agent", source)
    assert loaded["records"][0]["text"] == text
    assert loaded["records"][0]["summary_text"] == ""
    projected, stats = memory_summary_input.records([loaded])
    assert stats["eligible_records"] == 0 and stats["reference_only_records"] == 1
    assert stats["eligible_text_bytes"] == 0
    assert projected[0]["reference_only"]


def test_mixed_blocks_keep_agent_text_and_explicit_failure_only(memory_rig):
    _, _, _, tmp = memory_rig
    path, source = native(
        tmp,
        "claude-code",
        [
            {"type": "text", "text": "Keep the owner boundary."},
            {"type": "tool_result", "content": "SUCCESS BODY"},
            {"type": "tool_result", "content": "FAILURE BODY", "is_error": True},
            {"type": "text", "text": "The failure needs investigation."},
        ],
    )
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "I will run the check."},
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "SECRET COMMAND BODY"},
                            },
                        ],
                    },
                }
            )
            + "\n"
        )
    loaded = memory_sources.read_native("agent", source, retain=True)
    raw = "\n".join(r["text"] for r in loaded["records"])
    projected, _ = memory_summary_input.records([loaded])
    summary = "\n".join(r["text"] for r in projected)
    assert "SUCCESS BODY" in raw and "SECRET COMMAND BODY" in raw
    assert "SUCCESS BODY" not in summary and "SECRET COMMAND BODY" not in summary
    assert "FAILURE BODY" in summary
    assert "Keep the owner boundary." in summary and "I will run the check." in summary
    assert (
        loaded["records"]
        == memory_sources.read_native("agent", {**source, "snapshot": loaded["snapshot"]})[
            "records"
        ]
    )


def test_summary_excludes_noise_without_claiming_originals_were_summarized(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Keep original evidence; investigate the failing gate.")
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "content": "NATIVE SUCCESS NOISE " * 4000},
                        ],
                    },
                }
            )
            + "\n"
        )
    for identity, event_type, text in [
        ("command-input", "PreToolUse", "COMMAND INPUT NOISE " * 4000),
        ("command-output", "PostToolUse", "COMMAND OUTPUT NOISE " * 4000),
        ("failed-command", "PostToolUseFailure", "Failure: the fixture service is unavailable."),
        ("owner-prompt", "UserPromptSubmit", "Do not delete the fixture service."),
        ("agent-reply", "Stop", "The failing service is still unresolved."),
    ]:
        server.history.record(
            {
                "_id": identity,
                "_ts": 100,
                "event_type": event_type,
                "session_key": "agent",
                "test": True,
                "prompt": text,
            }
        )
    server.history.artifacts.register(
        "agent",
        {
            "source_path": "/test/noise-report.md",
            "title": "Fixture report",
            "content_base64": base64.b64encode(("ARTIFACT BODY NOISE " * 4000).encode()).decode(),
        },
    )
    calls = provider(monkeypatch)

    async def run():
        result = await server.progress_coordinator.refresh_result("agent")
        assert result.state == "updated"
        first = json.loads(calls[0].split("MEMORY UPDATE:\n", 1)[1])
        texts = "\n".join(r["text"] for r in first["records"])
        for noise in (
            "NATIVE SUCCESS NOISE",
            "COMMAND INPUT NOISE",
            "COMMAND OUTPUT NOISE",
            "ARTIFACT BODY NOISE",
        ):
            assert noise not in calls[0] and noise not in calls[1]
        assert "Failure: the fixture service is unavailable." in texts
        assert "Do not delete the fixture service." in texts
        assert "The failing service is still unresolved." in texts
        assert "command-output" in {r.get("event_id") for r in first["reference_only"]}
        continuity = result.revision["continuity"]
        stats = continuity["summary_input"]
        assert stats["eligible_text_bytes"] < stats["original_text_bytes"] / 10
        assert stats["reference_only_records"] >= 4
        assert stats["remaining_eligible_records"] == 0
        assert continuity["remaining_records"] >= stats["reference_only_records"]
        assert continuity["available_records"] == stats["original_records"]
        assert continuity["summarized_records"] == stats["eligible_records"]
        found = await server.memory.operate("agent", "search", {"q": ["COMMAND OUTPUT NOISE"]})
        assert found["results"]
        match = found["results"][0]
        read = await server.memory.operate(
            "agent", "read", {"source": [match["source"]], "record": [match["record_id"]]}
        )
        assert "COMMAND OUTPUT NOISE" in read["text"]

    asyncio.run(run())


def test_new_reference_only_event_refreshes_checkpoint_boundary(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Preserve the owner constraint.")
    calls = provider(monkeypatch)

    async def run():
        first = await server.progress_coordinator.refresh("agent")
        server.history.record(
            {
                "_id": "later-success",
                "_ts": 200,
                "event_type": "PostToolUse",
                "session_key": "agent",
                "test": True,
                "tool_response": "UNNEEDED BODY" * 5000,
            }
        )
        second = await server.progress_coordinator.refresh("agent")
        assert second["id"] != first["id"]
        assert second["source"] != first["source"]
        data = json.loads(calls[2].split("MEMORY UPDATE:\n", 1)[1])
        assert data["records"] == []
        assert data["prior_context"] == first["continuity"]["context"]
        assert "UNNEEDED BODY" not in calls[2]
        assert second["continuity"]["summary_input"]["remaining_eligible_records"] == 0
        checkpoint = await server._create_checkpoint(
            "agent", server.history.session("agent"), "manual"
        )
        assert checkpoint["record"]["summary_ref"] == second["id"]
        assert len(calls) == 4

    asyncio.run(run())
