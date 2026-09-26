import json
from datetime import UTC, datetime
from pathlib import Path

from duckterm.core.tokens import TokenLedger

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC).timestamp()


def claude(msg_id: str, day: str, cache_read: int, output: int) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": f"{day}T10:00:00Z",
            "requestId": "r-" + msg_id,
            "message": {
                "id": msg_id,
                "usage": {
                    "input_tokens": 1,
                    "cache_read_input_tokens": cache_read,
                    "output_tokens": output,
                },
            },
        }
    )


def codex(day: str, total_input: int, cached: int, output: int) -> str:
    usage = {"input_tokens": total_input, "cached_input_tokens": cached, "output_tokens": output}
    return json.dumps(
        {
            "timestamp": f"{day}T10:00:00Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": usage}},
        }
    )


def ledger(tmp_path: Path) -> TokenLedger:
    return TokenLedger(tmp_path / "claude", tmp_path / "codex")


def write(path: Path, lines: list[str], *, newline_at_end: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if newline_at_end else ""))


def test_claude_reply_split_across_lines_counts_once_and_subagents_count(tmp_path: Path) -> None:
    # Claude writes one line per content block, repeating the reply's usage.
    write(tmp_path / "claude/proj/s.jsonl", [claude("m1", "2026-09-25", 100, 5)] * 3)
    write(tmp_path / "claude/proj/s/subagents/agent-a.jsonl", [claude("m2", "2026-09-25", 40, 2)])
    totals = ledger(tmp_path).totals(7, NOW)["claude-code"]
    assert totals == {"input": 2, "cache_read": 140, "cache_write": 0, "output": 7}


def test_codex_running_totals_become_per_event_usage_within_the_window(tmp_path: Path) -> None:
    write(
        tmp_path / "codex/2026/09/10/rollout-a.jsonl",
        [
            codex("2026-09-10", 1000, 900, 10),  # outside the 7-day window
            codex("2026-09-24", 1500, 1300, 16),
            codex("2026-09-25", 1800, 1550, 20),
        ],
    )
    totals = ledger(tmp_path).totals(7, NOW)["codex"]
    assert totals == {"input": 150, "cache_read": 650, "cache_write": 0, "output": 10}


def test_rescan_reads_only_appended_lines_and_waits_for_a_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "claude/proj/s.jsonl"
    write(path, [claude("m1", "2026-09-25", 100, 5)])
    led = ledger(tmp_path)
    assert led.totals(7, NOW)["claude-code"]["output"] == 5
    with path.open("a") as fh:
        fh.write(claude("m2", "2026-09-25", 100, 7)[:40])  # half-written line
    assert led.totals(7, NOW)["claude-code"]["output"] == 5
    with path.open("a") as fh:
        fh.write(claude("m2", "2026-09-25", 100, 7)[40:] + "\n")
    assert led.totals(7, NOW)["claude-code"]["output"] == 12
