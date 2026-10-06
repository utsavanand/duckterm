from pathlib import Path

from duckterm.runtimes.codex import CodexRuntime


def test_detect_state_from_output_markers() -> None:
    rt = CodexRuntime()
    assert rt.detect_state("• Working (12s • esc to interrupt)") == "busy"
    assert rt.detect_state("Applying patch (3s)") == "busy"
    assert rt.detect_state("Allow this command? (y/n)") == "waiting"
    # Unrecognized output does not prove the turn ended.
    assert rt.detect_state("done.\n$ ") is None


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
    assert rt.restore_command(cwd=Path("/x"), session_key="abc-123") == [
        "codex",
        "resume",
        "abc-123",
    ]


def test_codex_resume_requires_recorded_identity(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """A directory match cannot establish which conversation belongs to a row."""
    import json

    uuid = "0192b256-a4a4-435c-b154-a9fe4be2c2a8"
    day = tmp_path / "home" / ".codex" / "sessions" / "2026" / "09" / "21"
    day.mkdir(parents=True)
    meta = {"type": "session_meta", "payload": {"cwd": str(tmp_path / "repo")}}
    (day / f"rollout-2026-09-21T10-00-00-{uuid}.jsonl").write_text(json.dumps(meta) + "\n")
    # A newer rollout for a DIFFERENT cwd must not win.
    other = {"type": "session_meta", "payload": {"cwd": str(tmp_path / "elsewhere")}}
    (day / "rollout-2026-09-21T11-00-00-ffffffff-ffff-ffff-ffff-ffffffffffff.jsonl").write_text(
        json.dumps(other) + "\n"
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    rt = CodexRuntime()
    assert rt.find_resumable_id(cwd=tmp_path / "repo", recorded=None) is None
    # Only a recorded id whose rollout exists is eligible.
    assert rt.find_resumable_id(cwd=tmp_path / "repo", recorded=uuid) == uuid
    # Nothing for an unknown cwd.
    assert rt.find_resumable_id(cwd=tmp_path / "nowhere", recorded=None) is None


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


def test_detect_state_ignores_code_text_and_reads_review_as_busy() -> None:
    from duckterm.runtimes.codex import CodexRuntime

    r = CodexRuntime()
    # codex's approval machinery running is WORK (interruptible-active line).
    assert r.detect_state("Reviewing approval request (5m 38s • esc to interrupt)") == "busy"
    # code on screen must not vote "waiting" (this exact word broke a session).
    assert r.detect_state('<iframe allow="autoplay" allowfullscreen>') is None
    assert r.detect_state("if approved_by_reviewer(x):") is None
    # real prompts still read as waiting.
    assert r.detect_state("Do you want to proceed?") == "waiting"


def test_conversation_prose_on_a_repaint_is_not_state() -> None:
    """2026-10-04: a repaint of ui-dev's idle screen carried its conversation,
    and words in it ("running", "would you like to") flipped a hook-stopped
    session to waiting, then busy, for two hours, so Oracle never nudged it."""
    idle_repaint = "\n".join(
        [
            "• I'm running the full gate now and reviewing the timeline preview.",
            "• Would you like to see the waving duck variant next?",
            "  Worked for 2m 28s · done 8:15 PM",
            "› Ask Codex to do anything",
            "  gpt-6-astra high · ~/worktrees/duckterm/4491fa9b",
        ]
    )
    with_status = idle_repaint.replace(
        "  Worked for 2m 28s", "• Working (3s • esc to interrupt)\n  Worked for 2m 28s"
    )
    rt = CodexRuntime()
    assert [rt.detect_state(idle_repaint), rt.detect_state(with_status)] == [None, "busy"]


def test_state_marker_explanations_are_not_live_screen_controls() -> None:
    runtime = CodexRuntime()
    prose = [
        "• The status line says esc to interrupt while a command runs.",
        "• The approval dialog asks Would you like to run the following command?",
    ]
    screens = [line + "\n› Ask Codex to do anything" for line in prose]
    assert [runtime.detect_state(screen) for screen in screens] == [None, None]
    assert runtime.detect_state("• Searching the web (12s • esc to interrupt)") == "busy"
    assert runtime.detect_state("Would you like to make the following edits?") == "waiting"
