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


@pytest.mark.parametrize(
    "role,text",
    [
        ("user", "check inbox"),
        ("user", "Check your inbox."),
        ("user", "You have something in your inbox."),
        ("assistant", "I’ll check the session inbox."),
    ],
)
def test_plain_inbox_notices_do_not_consume_record_allowance(role, text):
    assert memory_summary_input.is_routine_notice(role, text)


@pytest.mark.parametrize(
    "text",
    [
        "Check inbox. Fix the failed checkpoint before releasing.",
        "Do not send anything when you check your inbox.",
        'The summary incorrectly says "check inbox".',
        "sgtm",
        "yes",
    ],
)
def test_substantive_inbox_instructions_and_approvals_remain_originals(text):
    assert not memory_summary_input.is_routine_notice("user", text)


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
                {"PostToolUseFailure": "error", "Stop": "last_assistant_message"}.get(
                    event_type, "prompt"
                ): text,
            }
        )
    server.history.artifacts.register(
        "agent",
        {
            "source_path": "/test/noise-report.md",
            "title": "Fixture report",
            "kind": "report",
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
        assert "reference_only" not in first  # tool bodies and metadata are never prompt input
        continuity = result.revision["continuity"]
        assert continuity["remaining_records"] == 0
        assert continuity["verified"]
        assert continuity["summarized_records"] == len(first["records"])
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
        assert second["id"] == first["id"]
        assert len(calls) == 2  # successful tools do not generate another summary
        checkpoint = await server._create_checkpoint(
            "agent", server.history.session("agent"), "manual"
        )
        assert checkpoint["record"]["summary_ref"] == second["id"]
        assert len(calls) == 2
        assert checkpoint["summary_update"]["state"] == "reused"
        assert checkpoint["handoff_eligible"]

    asyncio.run(run())
