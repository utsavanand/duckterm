"""A saved conversation ID survives changing the session's working directory."""

import json

import pytest

from duckterm.runtimes.claude_code import ClaudeCodeRuntime, project_slug


@pytest.fixture
def relocated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    original = tmp_path / "project"
    current = original / "child"
    current.mkdir(parents=True)
    path = tmp_path / ".claude/projects" / project_slug(original) / "recorded.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "type": "user",
            "sessionId": "recorded",
            "cwd": str(original),
            "message": {"role": "user", "content": "Original request"},
        },
        {
            "type": "assistant",
            "sessionId": "recorded",
            "cwd": str(current),
            "message": {"role": "assistant", "content": "Continued in child"},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return original, current, path


def test_exact_recorded_transcript_survives_cwd_change(relocated):
    _, current, path = relocated
    runtime = ClaudeCodeRuntime()
    assert runtime.locate_transcript(cwd=current, session_id="recorded") == path
    assert runtime.find_resumable_id(cwd=current, recorded="recorded") == "recorded"
    assert (
        runtime.read_transcript(cwd=current, session_id="recorded")[0]["text"] == "Original request"
    )
    response = json.loads(runtime.messages_response(cwd=current, session_id="recorded"))
    assert len(response["messages"]) == 2


def test_relocated_lookup_never_substitutes_another_identity(relocated):
    _, current, _ = relocated
    runtime = ClaudeCodeRuntime()
    assert runtime.locate_transcript(cwd=current, session_id="missing") is None
    assert runtime.find_resumable_id(cwd=current, recorded=None) is None


def test_relocated_duplicate_is_ambiguous(relocated):
    _, current, path = relocated
    duplicate = path.parent.parent / "another-project" / path.name
    duplicate.parent.mkdir()
    duplicate.write_bytes(path.read_bytes())
    assert ClaudeCodeRuntime().locate_transcript(cwd=current, session_id="recorded") is None
    assert ClaudeCodeRuntime().messages(cwd=current, session_id="recorded") == []


@pytest.mark.parametrize("mutation", ["wrong-id", "missing-id", "wrong-project", "symlink"])
def test_relocated_file_requires_matching_metadata(relocated, mutation):
    _, current, path = relocated
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if mutation == "wrong-id":
        rows[-1]["sessionId"] = "peer"
    elif mutation == "missing-id":
        for row in rows:
            row.pop("sessionId")
    elif mutation == "wrong-project":
        rows[0]["cwd"] = str(current)
    if mutation == "symlink":
        real = path.with_suffix(".saved")
        path.rename(real)
        path.symlink_to(real)
    else:
        path.write_text("\n".join(json.dumps(row) for row in rows))
    assert ClaudeCodeRuntime().locate_transcript(cwd=current, session_id="recorded") is None
    assert ClaudeCodeRuntime().messages(cwd=current, session_id="recorded") == []


@pytest.mark.parametrize("identity", ["../recorded", "*", "", "/tmp/recorded"])
def test_relocated_identity_is_never_a_path_or_glob(relocated, identity):
    _, current, _ = relocated
    assert ClaudeCodeRuntime().locate_transcript(cwd=current, session_id=identity) is None
    assert ClaudeCodeRuntime().messages(cwd=current, session_id=identity) == []


def test_incomplete_project_scan_cannot_prove_uniqueness(relocated, monkeypatch):
    _, current, path = relocated
    (path.parent.parent / "unexamined").mkdir()
    monkeypatch.setattr("duckterm.runtimes.claude_code.MAX_PROJECT_DIRECTORIES", 1)
    assert ClaudeCodeRuntime().locate_transcript(cwd=current, session_id="recorded") is None
    assert ClaudeCodeRuntime().messages(cwd=current, session_id="recorded") == []


def test_current_project_match_keeps_native_lookup_precedence(relocated):
    _, current, path = relocated
    local = path.parent.parent / project_slug(current) / path.name
    local.parent.mkdir()
    local.write_bytes(path.read_bytes())
    assert ClaudeCodeRuntime().locate_transcript(cwd=current, session_id="recorded") == local


def test_metadata_without_messages_is_not_resumable(relocated):
    original, current, path = relocated
    path.write_text(json.dumps({"sessionId": "recorded", "cwd": str(original)}))
    assert ClaudeCodeRuntime().locate_transcript(cwd=current, session_id="recorded") is None
    assert ClaudeCodeRuntime().messages(cwd=current, session_id="recorded") == []


def test_tool_content_is_a_message_without_a_user_text_prompt(relocated):
    original, current, path = relocated
    path.write_text(
        json.dumps(
            {
                "sessionId": "recorded",
                "cwd": str(original),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Read", "input": {}}],
                },
            }
        )
    )
    assert ClaudeCodeRuntime().locate_transcript(cwd=current, session_id="recorded") == path


def test_empty_duplicate_still_blocks_cross_project_lookup(relocated):
    _, current, path = relocated
    duplicate = path.parent.parent / "another-project" / path.name
    duplicate.parent.mkdir()
    duplicate.touch()
    assert ClaudeCodeRuntime().locate_transcript(cwd=current, session_id="recorded") is None
    assert ClaudeCodeRuntime().messages(cwd=current, session_id="recorded") == []
