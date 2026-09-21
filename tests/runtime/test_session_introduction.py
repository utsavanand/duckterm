"""Agent introduction reaches launch/input paths without replacing user intent."""

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from duckterm.core.orchestrator import SessionSupervisor
from duckterm.helpers.session_instructions import GUIDE, launch_prompt
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.runtimes.codex import CodexRuntime
from duckterm.server import Server


@pytest.mark.parametrize("runtime", [CodexRuntime(), ClaudeCodeRuntime()])
def test_supervised_launch_preserves_user_task_and_enrolls_before_spawn(
    tmp_path, monkeypatch, runtime
):
    store = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=store)
    captured = []
    monkeypatch.setattr("duckterm.core.orchestrator.tmux.has_tmux", lambda: False)

    async def spawn(supervisor, argv):
        credential = json.loads(Path(supervisor._env["DUCKTERM_SESSION_TOKEN_FILE"]).read_text())
        assert credential["session_id"] == "new-agent"
        assert credential["token"] not in argv[-1]
        captured.extend(argv)

    monkeypatch.setattr(SessionSupervisor, "_start_pty", spawn)
    task = "Fix billing.\nKeep the user's $variables and `code` intact."
    asyncio.run(
        server.orchestrator.launch(
            runtime=runtime,
            cwd=str(tmp_path),
            session_key="new-agent",
            prompt=task,
        )
    )
    assert captured[-1].startswith("Duckterm session capability:")
    assert captured[-1].endswith("User's task:\n" + task)
    assert store.session("new-agent")["intention"] == task
    guide = next((tmp_path / "session-instructions").glob("*/collaboration.md"))
    assert guide.read_text() == GUIDE
    assert guide.stat().st_mode & 0o777 == 0o600
    assert str(guide) in captured[-1]
    store.close()


def test_blank_launch_and_unsupported_runtime(tmp_path):
    prompt = launch_prompt("codex", "../../legacy", "", home=tmp_path)
    assert "await the user's task" in prompt
    assert len(list((tmp_path / "session-instructions").glob("*/collaboration.md"))) == 1
    for runtime in ("generic", "copilot"):
        assert launch_prompt(runtime, "other", "unchanged", home=tmp_path) == "unchanged"


def test_owner_introduction_requires_idle_live_agent(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "db.sqlite")
    store.record(
        {
            "_id": "start",
            "_ts": 1,
            "session_key": "existing",
            "event_type": "SessionStart",
            "runtime": "codex",
        }
    )
    server = Server(history=store)
    writes = []
    supervisor = SimpleNamespace(running=True, write_bytes=lambda data: writes.append(data) or True)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: supervisor)

    def post(port, token, action="introduce"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/sessions/existing/collaboration/{action}",
            data=b"{}",
            headers={"X-Duckterm-Token": token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    async def scenario():
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        async with listener:
            assert (await asyncio.to_thread(post, port, "invalid"))[0] == 401
            for state in ("busy", "waiting", "stopped"):
                store.set_state("existing", state, now=2)
                assert (await asyncio.to_thread(post, port, server.token))[0] == 409
                assert writes == []
            store.set_state("existing", "busy", now=3)
            status, preview = await asyncio.to_thread(post, port, server.token, "instructions")
            assert status == 200 and preview["prompt"].startswith("Duckterm session capability:")
            assert writes == []
            store.set_state("existing", "idle", now=3)
            assert await asyncio.to_thread(post, port, server.token) == (200, {"sent": True})
            supervisor.running = False
            assert (await asyncio.to_thread(post, port, server.token))[0] == 409

    asyncio.run(scenario())
    assert len(writes) == 1
    assert writes[0].startswith(b"\x1b[200~Duckterm session capability:")
    assert writes[0].endswith(b"\x1b[201~\r")
    assert server.token.encode() not in writes[0]
    store.close()


def test_launch_prompt_injects_runtime_scoped_rules(tmp_path):
    """Scoped active rules from the folder's typed rule set ride the launch
    prompt for the matching runtime; "all" rules stay out (AGENTS.md itself
    reaches every agent), as do candidates and other runtimes' rules."""
    import json as _json

    workdir = tmp_path / "proj"
    workdir.mkdir()
    (workdir / ".duckterm-rules.json").write_text(
        _json.dumps(
            {
                "rules": [
                    {"id": "shared", "text": "Shared rule.", "status": "active"},
                    {"id": "codex-r", "text": "Codex rule.", "scope": "codex", "status": "active"},
                    {
                        "id": "claude-r",
                        "text": "Claude rule.",
                        "scope": "claude-code",
                        "status": "active",
                    },
                    {
                        "id": "pending",
                        "text": "Pending rule.",
                        "scope": "codex",
                        "status": "candidate",
                    },
                ]
            }
        )
    )
    prompt = launch_prompt("codex", "k1", "do the task", home=tmp_path, cwd=workdir)
    assert "Codex rule." in prompt
    assert "Shared rule." not in prompt
    assert "Claude rule." not in prompt
    assert "Pending rule." not in prompt
    assert prompt.endswith("do the task")

    # Corrupt rules.json must not block a launch.
    (workdir / ".duckterm-rules.json").write_text("{nope")
    prompt = launch_prompt("codex", "k2", "still works", home=tmp_path, cwd=workdir)
    assert prompt.endswith("still works")
