"""Structured message parsing for the HTML/pagination views: parse_messages
keeps message identity and ordered content blocks (text/tool_use/tool_result),
unlike the flat parse_transcript used by the summarizer."""

import json
import tempfile
from pathlib import Path

from duckterm.runtimes.claude_code import parse_messages


def _transcript(lines: list[dict]) -> Path:
    f = Path(tempfile.mkdtemp()) / "t.jsonl"
    f.write_text("\n".join(json.dumps(line) for line in lines))
    return f


def test_parse_messages_keeps_blocks_and_skips_bookkeeping() -> None:
    path = _transcript(
        [
            {"type": "mode"},  # bookkeeping — skipped
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}}],
                },
            },
            {"type": "ai-title"},  # bookkeeping — skipped
        ]
    )
    msgs = parse_messages(path)

    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]
    assert msgs[0]["blocks"] == [{"type": "text", "text": "hi"}]
    assert msgs[1]["blocks"] == [{"type": "text", "text": "hello"}]
    assert msgs[2]["blocks"][0]["type"] == "tool_use"
    assert msgs[2]["blocks"][0]["name"] == "Bash"
    # ids are stable line indices (used as annotation anchors).
    assert msgs[0]["id"] == 1 and msgs[1]["id"] == 2


def test_parse_messages_skips_system_injected_user_turns() -> None:
    """Claude Code records task notifications and hook reminders as user
    messages. They must not render as 'you' or anchor the latest-reply view."""
    path = _transcript(
        [
            {
                "type": "user",
                "promptSource": "typed",
                "message": {"role": "user", "content": "real prompt"},
            },
            {
                "type": "user",
                "promptSource": "system",
                "origin": {"kind": "task-notification"},
                "message": {"role": "user", "content": "<task-notification>…</task-notification>"},
            },
            {
                "type": "user",
                "isMeta": True,
                "message": {"role": "user", "content": "<system-reminder>…</system-reminder>"},
            },
            {
                "type": "user",
                "promptSource": "queued",
                "message": {"role": "user", "content": "queued while busy — still a real prompt"},
            },
            # Slash-command records have no promptSource marker at all — the
            # machine markup itself is the signal.
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "<local-command-stdout>Compacted…</local-command-stdout>",
                },
            },
            {
                "type": "user",
                "message": {"role": "user", "content": "<command-name>/compact</command-name>"},
            },
        ]
    )
    texts = [m["blocks"][0]["text"] for m in parse_messages(path)]
    assert texts == ["real prompt", "queued while busy — still a real prompt"]
