from pathlib import Path

from duckterm.runtimes.codex import CodexRuntime


def test_detect_state_from_output_markers() -> None:
    rt = CodexRuntime()
    assert rt.detect_state("Working on it...") == "busy"
    assert rt.detect_state("applying patch to foo.py") == "busy"
    assert rt.detect_state("Allow this command? (y/n)") == "waiting"
    # Settled output with no marker reads as idle.
    assert rt.detect_state("done.\n$ ") == "idle"


def test_waiting_takes_precedence_over_working() -> None:
    rt = CodexRuntime()
    assert rt.detect_state("working\nApprove edit? (y/n)") == "waiting"


def test_launch_appends_prompt() -> None:
    rt = CodexRuntime("codex")
    assert rt.launch_command(cwd=Path("/x"), session_key="s", initial_prompt="add tests") == [
        "codex",
        "add tests",
    ]


def test_codex_has_no_transcript_yet() -> None:
    rt = CodexRuntime()
    assert rt.locate_transcript(cwd=Path("/x"), session_id="s") is None
    assert rt.restore_command(cwd=Path("/x"), session_key="s") == ["codex"]


def test_parse_codex_transcript_extracts_messages(tmp_path):  # type: ignore[no-untyped-def]
    import json

    from duckterm.runtimes.codex import parse_codex_transcript

    t = tmp_path / "rollout.jsonl"
    t.write_text(
        "\n".join(
            json.dumps(o)
            for o in [
                {"type": "session_meta", "payload": {"id": "x"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "fix the bug"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Fixed it in store.ts"}],
                    },
                },
                {"type": "event_msg", "payload": {"type": "user_message"}},
            ]
        )
    )
    records = parse_codex_transcript(t)
    assert records == [
        {"role": "user", "text": "fix the bug"},
        {"role": "assistant", "text": "Fixed it in store.ts"},
    ]


def _rollout_line(ptype: str, **payload) -> str:
    import json

    return json.dumps({"type": "response_item", "payload": {"type": ptype, **payload}})


def test_parse_codex_messages_structured_blocks(tmp_path):  # type: ignore[no-untyped-def]
    import json

    from duckterm.runtimes.codex import parse_codex_messages

    lines = [
        json.dumps({"type": "session_meta", "payload": {"cwd": "/repo"}}),
        _rollout_line(
            "message", role="developer", content=[{"type": "input_text", "text": "instructions"}]
        ),
        _rollout_line(
            "message",
            role="user",
            content=[{"type": "input_text", "text": "<environment_context>\n<cwd>/repo</cwd>"}],
        ),
        _rollout_line(
            "message", role="user", content=[{"type": "input_text", "text": "fix the bug"}]
        ),
        _rollout_line("reasoning", encrypted_content="xxx"),
        _rollout_line("function_call", name="shell", arguments='{"command": "ls"}'),
        _rollout_line("function_call_output", output="file.py"),
        _rollout_line(
            "message", role="assistant", content=[{"type": "output_text", "text": "done"}]
        ),
    ]
    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(lines))

    records = parse_codex_messages(path)
    assert [(r["role"], r["blocks"][0]["type"]) for r in records] == [
        ("user", "text"),
        ("assistant", "tool_use"),
        ("assistant", "tool_result"),
        ("assistant", "text"),
    ]
    assert records[0]["blocks"][0]["text"] == "fix the bug"  # env-context turn skipped
    assert records[1]["blocks"][0] == {
        "type": "tool_use",
        "name": "shell",
        "input": {"command": "ls"},
    }
    assert records[2]["blocks"][0]["text"] == "file.py"
    assert len({r["id"] for r in records}) == 4  # ids are stable line indexes


def test_latest_transcript_matches_cwd(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    import json

    from duckterm.runtimes.codex import CodexRuntime

    monkeypatch.setenv("HOME", str(tmp_path))
    day = tmp_path / ".codex" / "sessions" / "2026" / "09" / "20"
    day.mkdir(parents=True)
    (day / "rollout-2026-09-20T10-00-00-aaa.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": "/other"}}) + "\n"
    )
    (day / "rollout-2026-09-20T11-00-00-bbb.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": "/repo"}}) + "\n"
    )

    from pathlib import Path

    assert CodexRuntime().latest_transcript(cwd=Path("/repo")) == (
        day / "rollout-2026-09-20T11-00-00-bbb.jsonl"
    )
    assert CodexRuntime().latest_transcript(cwd=Path("/nowhere")) is None


def test_detect_state_recognizes_real_codex_approval_prompt() -> None:
    from duckterm.runtimes.codex import CodexRuntime

    r = CodexRuntime()
    assert r.detect_state("Would you like to run the following command?") == "waiting"
    assert r.detect_state("Press enter to confirm or esc to cancel") == "waiting"
    assert r.detect_state("Working (17m 36s • esc to interrupt)") == "busy"
    # A live prompt below an older Working line wins (bottom-up scan).
    assert r.detect_state("Working (2s)\nWould you like to run the following command?") == "waiting"
    # And fresh work below an old prompt reads busy.
    assert r.detect_state("Press enter to confirm\nWorking (17m 36s)") == "busy"
