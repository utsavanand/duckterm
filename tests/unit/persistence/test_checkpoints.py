import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from duckterm.persistence.checkpoints import (
    build_checkpoint,
    load_record,
    markdown_dir,
    write_markdown,
)


@pytest.fixture(autouse=True)
def no_summarizer() -> Iterator[None]:
    saved = {
        k: os.environ.pop(k, None) for k in ("DUCKTERM_SUMMARIZER_CMD", "DUCKTERM_SUMMARIZER_URL")
    }
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def events() -> list[dict]:
    return [
        {"event_type": "UserPromptSubmit", "prompt": "add a login form"},
        {"event_type": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "login.tsx"}},
        {"event_type": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "login.tsx"}},
        {"event_type": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "npm test"}},
        {"event_type": "UserPromptSubmit", "prompt": "now add validation"},
    ]


def test_checkpoint_captures_prompts_files_tools(tmp_path: Path) -> None:
    cp = build_checkpoint(
        session_key="s1",
        label="manual",
        cwd=tmp_path,
        events=events(),
        intention="build auth",
        now_ms=1000,
    )
    r = cp.record
    assert r["prompts"] == ["add a login form", "now add validation"]
    assert r["files"] == [{"path": "login.tsx", "edits": 2}]
    assert {"tool": "Edit", "count": 2} in r["tools"]
    assert r["event_count"] == 5


def claude_hook_events() -> list[dict]:
    """Mirrors what duckterm-hook.sh forwards for a watched claude-code
    session: UserPromptSubmit carries a top-level `prompt`; Bash PreToolUse and
    PostToolUse both fire, with the command under tool_input.command."""
    return [
        {"event_type": "UserPromptSubmit", "prompt": "run the tests", "_ts": 1},
        {
            "event_type": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "_ts": 2,
        },
        {
            "event_type": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "_ts": 3,
        },
        {
            "event_type": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "_ts": 4,
        },
        {
            "event_type": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "_ts": 5,
        },
    ]


def test_checkpoint_captures_commands_and_prompts_from_hook_events(tmp_path: Path) -> None:
    cp = build_checkpoint(
        session_key="s1",
        label="manual",
        cwd=tmp_path,
        events=claude_hook_events(),
        intention="",
        now_ms=1000,
    )
    r = cp.record
    assert r["prompts"] == ["run the tests"]
    # Each command recorded once (on PreToolUse), in execution order — the
    # paired PostToolUse must not double-count it.
    assert r["commands"] == ["pytest -q", "git status"]
    md = cp.markdown  # rendered text; build_checkpoint no longer writes to disk
    assert "## Commands run" in md
    assert "`pytest -q`" in md
    assert "run the tests" in md


def test_checkpoint_records_git_state(git_repo: Path, tmp_path: Path) -> None:
    cp = build_checkpoint(
        session_key="s1",
        label="m",
        cwd=git_repo,
        events=events(),
        intention="",
        now_ms=1000,
    )
    assert cp.record["git"] is True
    assert cp.record["repo"] == git_repo.name
    assert cp.record["branch"] in ("main", "master")


def test_checkpoint_notes_non_git_dir(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    cp = build_checkpoint(session_key="s1", label="m", cwd=plain, events=[], intention="", now_ms=1)
    assert cp.record["git"] is False
    assert cp.record["path"] == str(plain)


def test_mechanical_summary_when_no_llm(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DUCKTERM_SUMMARIZER", "off")  # force the no-LLM path
    cp = build_checkpoint(
        session_key="s1",
        label="m",
        cwd=tmp_path,
        events=events(),
        intention="build auth",
        now_ms=1,
    )
    # No summarizer -> mechanical fallback mentioning intent + activity.
    assert "build auth" in cp.summary
    assert "5 events" in cp.summary


def test_build_renders_markdown_without_writing(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # build_checkpoint is pure: it renders text into cp.markdown and writes
    # NOTHING to disk (so a crash before the row exists can't orphan a file).
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    cp = build_checkpoint(
        session_key="s1", label="m", cwd=tmp_path, events=events(), intention="", now_ms=1
    )
    assert cp.markdown_path is None
    assert "add a login form" in cp.markdown
    assert "login.tsx" in cp.markdown
    assert not (tmp_path / "home" / "checkpoints").exists()  # nothing written yet


def test_write_markdown_uses_the_stable_home_root_and_relative_path(
    tmp_path: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    # write_markdown puts the file under DUCKTERM_HOME/checkpoints/<key>/ (NOT
    # the worktree) and returns a path RELATIVE to that root, so it survives a
    # moved home / deleted worktree. load_record resolves it back.
    home = tmp_path / "home"
    monkeypatch.setenv("DUCKTERM_HOME", str(home))
    rel = write_markdown("s1", 1000, "# hello\n")
    assert rel == "s1/checkpoint-1000.md"  # relative, not absolute
    assert (home / "checkpoints" / "s1" / "checkpoint-1000.md").is_file()
    assert (home / "checkpoints" / "s1" / "latest.md").is_file()
    assert load_record(rel) == "# hello\n"  # resolves relative -> home root


def test_load_record_still_reads_legacy_absolute_paths(tmp_path: Path) -> None:
    # Old rows stored an absolute path (pre stable-root); those must still read.
    p = tmp_path / "old.md"
    p.write_text("legacy\n")
    assert load_record(str(p)) == "legacy\n"


def test_markdown_dir_points_under_home(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    assert markdown_dir("s1") == tmp_path / "home" / "checkpoints" / "s1"


def test_checkpoint_captures_all_prompts_untruncated(tmp_path: Path) -> None:
    long_prompt = "x" * 500  # would have been truncated to 240 before
    evs = [{"event_type": "UserPromptSubmit", "prompt": long_prompt, "_ts": i} for i in range(20)]
    cp = build_checkpoint(
        session_key="s", label="cp", cwd=tmp_path, events=evs, intention="", now_ms=1000
    )
    # All 20 prompts kept (not capped at 12), full length (not cut to 240).
    assert len(cp.record["prompts"]) == 20
    assert cp.record["prompts"][0] == long_prompt


def test_checkpoint_delta_since_last_checkpoint(tmp_path: Path) -> None:
    evs = [
        {"event_type": "UserPromptSubmit", "prompt": "first", "_ts": 100},
        {"event_type": "UserPromptSubmit", "prompt": "second", "_ts": 300},
    ]
    cp = build_checkpoint(
        session_key="s",
        label="cp2",
        cwd=tmp_path,
        events=evs,
        intention="",
        now_ms=400,
        since_ms=200,  # the previous checkpoint was at ts=200
    )
    # Full record still has both prompts...
    assert cp.record["prompts"] == ["first", "second"]
    # ...but the delta only has the one after ts=200.
    assert cp.record["new_since_last"]["prompts"] == ["second"]
    assert cp.record["new_since_last"]["event_count"] == 1
