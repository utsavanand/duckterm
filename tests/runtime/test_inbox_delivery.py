import asyncio
import json
import time

import pytest

from duckterm.cli import build_parser
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    history = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=history)
    tokens = {}
    for key in ("sender", "recipient"):
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "test": True,
                "runtime": "claude-code",
            }
        )
        history.set_meta(key, group="team")
        token = history.session_api.enroll(key, {"root": "team"})["token"]
        tokens[key] = {"authorization": "Bearer " + token}
    history.set_state("recipient", "idle")

    def call(who, method, path, body=None, idem="one"):
        return history.session_api.handle(
            method,
            "/api/v1/session" + path,
            {**tokens[who], "idempotency-key": idem},
            json.dumps(body or {}).encode(),
        )[1]

    def ask(idem="one", timeout=0):
        return call(
            "sender",
            "POST",
            "/questions",
            {
                "target_session_id": "recipient",
                "question": "Peer-controlled text MUST NOT be typed",
                "timeout_seconds": timeout,
            },
            idem,
        )

    yield history, server, call, ask
    history.close()


def test_default_persistent_acceptance_and_restart(scenario, monkeypatch, tmp_path):
    history, _, call, ask = scenario
    question = ask()
    assert question["expires_at"] == 0
    # Legacy servers use expires_at <= now without a persistence flag.
    stored = history._conn.execute(
        "SELECT expires_at FROM session_questions WHERE id = ?", (question["id"],)
    ).fetchone()[0]
    assert stored > (time.time() + 30 * 86400) * 1000
    call("recipient", "POST", f"/questions/{question['id']}/accept")
    future = time.time() + 30 * 86400
    monkeypatch.setattr("duckterm.core.session_api.time.time", lambda: future)
    assert call("recipient", "GET", "/inbox")["messages"][0]["status"] == "accepted"
    reopened = HistoryStore(tmp_path / "db.sqlite")
    assert (
        reopened.session_api.inbox("recipient", owner=True)["messages"][0]["status"] == "accepted"
    )
    reopened.close()
    assert build_parser().parse_args(["session", "ask", "recipient", "task"]).timeout == 0


def test_explicit_deadline_still_expires_after_acceptance(scenario, monkeypatch):
    _, _, call, ask = scenario
    question = ask(timeout=30)
    call("recipient", "POST", f"/questions/{question['id']}/accept")
    monkeypatch.setattr(
        "duckterm.core.session_api.time.time", lambda: question["expires_at"] / 1000 + 1
    )
    assert call("recipient", "GET", "/inbox")["messages"][0]["status"] == "expired"


def accept_question(call, ask, idem="one"):
    question = ask(idem)
    call("recipient", "POST", f"/questions/{question['id']}/accept")
    return question


def notice(server, **extra):
    return server._inbox_hook_output(
        {"event_type": "Stop", "stop_hook_active": False, **extra}, "recipient"
    )


def test_notice_once_per_accepted_assignment_and_survives_restart(scenario, tmp_path):
    history, server, call, ask = scenario
    assert notice(server) is None
    ask("queued")
    assert notice(server) is None
    accept_question(call, ask)
    output = notice(server)
    assert output["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert "1 accepted" in output["hookSpecificOutput"]["additionalContext"]
    assert "Peer-controlled" not in json.dumps(output)
    assert notice(server) is None
    reopened = HistoryStore(tmp_path / "db.sqlite")
    assert reopened.session_api.turn_end_notice("recipient") is None
    reopened.close()
    accept_question(call, ask, "new")
    assert notice(server) is not None
    assert history.session_api.pending_counts() == {"recipient": 3}


@pytest.mark.parametrize(
    "reason",
    ["waiting", "stopped", "archived", "approval", "loop", "codex", "unknown", "other_event"],
)
def test_notice_suppression_does_not_consume_assignment(scenario, reason):
    history, server, call, ask = scenario
    accept_question(call, ask)
    extra = {}
    if reason in {"waiting", "stopped", "archived"}:
        history.set_state("recipient", reason)
    elif reason == "approval":
        server.approvals.register("recipient", "Bash", {}, 1, blocking=True)
    elif reason == "loop":
        extra["stop_hook_active"] = True
    elif reason == "other_event":
        extra["event_type"] = "PostToolUse"
    else:
        history._conn.execute(
            "UPDATE sessions SET runtime = ? WHERE session_key = 'recipient'", (reason,)
        )
    assert notice(server, **extra) is None
    assert (
        history._conn.execute(
            "SELECT COUNT(*) FROM session_inbox_delivery WHERE last_attempt_at > 0"
        ).fetchone()[0]
        == 0
    )


def test_owner_view_is_not_agent_read(scenario):
    history, _, call, ask = scenario
    ask()
    assert (
        history.session_api.inbox("recipient", owner=True)["messages"][0]["delivery"]["attempts"]
        == 0
    )
    call("recipient", "GET", "/inbox")
    assert (
        history.session_api.inbox("recipient", owner=True)["messages"][0]["delivery"][
            "last_read_at"
        ]
        > 0
    )


def test_turn_end_hook_is_synchronous_only_where_supported():
    from duckterm.agents.hooks_install import claude_style_build

    claude = claude_style_build({}, "/hook", "claude-code")["hooks"]
    assert claude["Stop"][0]["hooks"][0]["async"] is False
    assert claude["PostToolUse"][0]["hooks"][0]["async"] is True
    codex = claude_style_build({}, "/hook", "codex")["hooks"]
    assert "async" not in codex["Stop"][0]["hooks"][0]


def test_ingest_checks_waiting_before_stop_fold(scenario, monkeypatch):
    history, server, call, ask = scenario
    accept_question(call, ask)
    history.set_state("recipient", "waiting")
    responses = []

    async def capture(writer, status, body):
        responses.append(body)

    monkeypatch.setattr("duckterm.server._write_json", capture)
    monkeypatch.setattr(server, "_maybe_refresh_progress", lambda key: None)
    asyncio.run(
        server._ingest(
            None,
            json.dumps(
                {"event_type": "Stop", "session_key": "recipient", "stop_hook_active": False}
            ).encode(),
        )
    )
    assert "hook_output" not in responses[0]


@pytest.mark.parametrize("cancelled", [False, True])
def test_notice_respects_visibility_and_cancellation(scenario, cancelled):
    history, server, call, ask = scenario
    question = accept_question(call, ask)
    if cancelled:
        call("sender", "POST", f"/questions/{question['id']}/cancel")
    else:
        history.set_meta("recipient", group="elsewhere")
        history.session_api.enroll("recipient", {"root": "elsewhere"})
    assert notice(server) is None


def test_stop_shell_forwards_only_hook_response_and_loop_guard(tmp_path):
    import os
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("jq"):
        pytest.skip("jq unavailable")
    binary = tmp_path / "bin"
    binary.mkdir()
    capture = tmp_path / "payload"
    curl = binary / "curl"
    response = {
        "hook_output": {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": "Inbox work exists",
            }
        },
        "private_event": "must not reach agent",
    }
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CAPTURE']).write_text(sys.argv[sys.argv.index('-d') + 1])\n"
        f"print({json.dumps(response)!r})\n"
    )
    curl.chmod(0o700)
    script = Path(__file__).parents[2] / "src/duckterm/hooks/duckterm-hook.sh"
    result = subprocess.run(
        ["bash", str(script), "Stop", "claude-code"],
        input='{"session_id":"test","stop_hook_active":false}',
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
    assert json.loads(result.stdout) == response["hook_output"]
    assert json.loads(capture.read_text())["stop_hook_active"] is False
