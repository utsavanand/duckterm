"""Questions an agent puts to the owner through its own tool become Oracle
"choice" notes, for every harness that declares one (B16, 2026-10-01). The
payloads are real: Codex 0.155.1 from the owner's database, Copilot CLI
1.0.62 from a probe with a logging hook."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from tests.runtime.test_oracle import CODEX_EMPTY
from tests.runtime.test_session_api import dispatch

from duckterm.core.relay import choices_from
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.codex import CodexRuntime
from duckterm.server import Server

CODEX_ASK = {
    "questions": [
        {
            "title": "May I push fix/message-transcript-cache and fix/fork-child-identity to the "
            "public utsavanand/duckterm repository and open PRs? Both gates are green.",
            "options": ["Yes, publish both branches and open PRs", "No, keep both local"],
        }
    ]
}
COPILOT_RAW = {
    "sessionId": "4b2dc93a-3efa-45f7-8c65-dc0bbd7793c4",
    "timestamp": 1790846696910,
    "cwd": "/tmp/cp",
    "toolName": "ask_user",
    "toolArgs": {
        "message": "Pick a colour",
        "requestedSchema": {
            "properties": {
                "colour": {
                    "type": "string",
                    "title": "Colour",
                    "oneOf": [{"const": "Red", "title": "Red"}, {"const": "Blue", "title": "Blue"}],
                    "default": "Red",
                }
            }
        },
    },
}
# Codex 0.155.1 with a question queued: the input box below is empty.
CODEX_QUEUED = (
    "• Queued follow-up inputs\n  ? 1 question · 7s\n    shift + ← to answer\n" + CODEX_EMPTY
)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    for key, runtime in [("ui", "codex"), ("cp", "copilot"), ("cc", "claude-code")]:
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "test": True,
                "runtime": runtime,
            }
        )
        history.set_meta(key, name=key, group="Duckterm")
    yield Server(history=history)
    history.close()


def choices(server, key):
    notes = dispatch(server, "GET", "/relay", {})[1]["notes"]
    return [(n["status"], n["questions"]) for n in notes if n["session_key"] == key]


def event(key, event_type, **extra):
    return {"event_type": event_type, "session_key": key, **extra}


def test_codex_question_is_a_note_until_a_prompt_is_submitted(server) -> None:
    ask = {"tool_name": "request_user_input_async", "tool_input": CODEX_ASK}
    server.bus.publish(event("ui", "PreToolUse", **ask))
    # The tool returns at once and the agent carries on; the question stays.
    server.bus.publish(event("ui", "PostToolUse", **ask))
    server.bus.publish(event("ui", "PreToolUse", tool_name="Bash"))
    server.bus.publish(event("ui", "Stop"))
    question = CODEX_ASK["questions"][0]
    assert choices(server, "ui") == [
        ("open", [{"question": question["title"], "options": question["options"]}])
    ]
    assert dispatch(server, "GET", "/relay/count", {})[1] == {"open": 1}  # Needs you

    answer = f"> {question['title']}\n\nYes, publish both branches and open PRs"
    server.bus.publish(event("ui", "UserPromptSubmit", prompt=answer))
    assert [status for status, _ in choices(server, "ui")] == ["handled"]


def test_copilot_question_from_the_hook_payload_closes_when_answered(server) -> None:
    server.bus.publish(
        event("cp", "PreToolUse", tool_name="ask_user", tool_input=COPILOT_RAW["toolArgs"])
    )
    assert choices(server, "cp") == [
        ("open", [{"question": "Pick a colour", "options": ["Red", "Blue"]}])
    ]
    server.bus.publish(event("cp", "PostToolUse", tool_name="ask_user"))  # Copilot waits
    assert [status for status, _ in choices(server, "cp")] == ["handled"]


def test_a_tool_another_harness_uses_to_ask_is_not_a_question_here(server) -> None:
    server.bus.publish(
        event("cc", "PreToolUse", tool_name="request_user_input_async", tool_input=CODEX_ASK)
    )
    assert choices(server, "cc") == []


@pytest.mark.parametrize(
    ("tool_input", "expected"),
    [
        (CODEX_ASK, [(CODEX_ASK["questions"][0]["title"], CODEX_ASK["questions"][0]["options"])]),
        (
            {"questions": [{"title": "Which session did you stop?"}]},
            [("Which session did you stop?", [])],
        ),
        (COPILOT_RAW["toolArgs"], [("Pick a colour", ["Red", "Blue"])]),
        (json.dumps(COPILOT_RAW["toolArgs"]), [("Pick a colour", ["Red", "Blue"])]),
        (
            {
                "message": "Ship?",
                "requestedSchema": {"properties": {"go": {"enum": ["yes", "no"]}}},
            },
            [("Ship?", ["yes", "no"])],
        ),
        (
            {
                "questions": [
                    {"question": "Merge?", "options": [{"label": "Yes"}, {"label": "Hold"}]}
                ]
            },
            [("Merge?", ["Yes", "Hold"])],
        ),
        (None, []),
    ],
)
def test_choices_from_each_harness_shape(tool_input, expected) -> None:
    assert choices_from(tool_input) == expected


def test_a_queued_codex_question_keeps_oracle_from_typing() -> None:
    """Anything submitted while it's queued discards it (checked on Codex
    0.155.1), so Oracle's reminder must wait like it does for a draft."""
    codex = CodexRuntime()
    assert codex.prompt_is_empty(CODEX_EMPTY)
    assert not codex.prompt_is_empty(CODEX_QUEUED)


def test_the_hook_forwards_copilots_tool_args(tmp_path) -> None:
    if not shutil.which("jq"):
        pytest.skip("jq unavailable")
    binary = tmp_path / "bin"
    binary.mkdir()
    capture = tmp_path / "payload"
    curl = binary / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CAPTURE']).write_text(sys.argv[sys.argv.index('-d') + 1])\n"
    )
    curl.chmod(0o700)
    script = Path(__file__).parents[2] / "src/duckterm/hooks/duckterm-hook.sh"
    subprocess.run(
        ["bash", str(script), "PreToolUse", "copilot"],
        input=json.dumps(COPILOT_RAW),
        text=True,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "DUCKTERM_INTERNAL": "",
            "DUCKTERM_HOME": str(tmp_path),
            "CAPTURE": str(capture),
        },
    )
    deadline = time.time() + 5  # the event post runs in the background
    while not capture.exists() and time.time() < deadline:
        time.sleep(0.05)
    payload = json.loads(capture.read_text())
    assert (payload["tool_name"], payload["tool_input"]) == ("ask_user", COPILOT_RAW["toolArgs"])
