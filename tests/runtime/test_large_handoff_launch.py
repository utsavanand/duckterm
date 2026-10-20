"""The real Codex switch launch carries a full brief through tmux unchanged."""

import asyncio
import json
import shlex
import shutil
import sys

import pytest

from duckterm.harness_switch import launch
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_switch_launch_delivers_large_brief_on_same_card(tmp_path):
    fake = tmp_path / "codex"
    script = shlex.join([sys.executable, str(tmp_path / "fake.py")])
    fake.write_text(f'#!/bin/sh\nexec {script} "$@"\n')
    fake.chmod(0o700)
    (tmp_path / "fake.py").write_text(
        "import json,os,pathlib,sys,time\n"
        "pathlib.Path('received.json').write_text(json.dumps({"
        "'args':sys.argv[1:],'key':os.environ['DUCKTERM_SESSION_KEY']}))\n"
        "print('HANDOFF_READY',flush=True)\n"
        "time.sleep(30)\n"
    )
    brief = ("Owner's complete handoff, including `literal tools` and $variables.\n" * 600)[:32000]
    store = HistoryStore(tmp_path / "history.db")
    server = Server(history=store)
    key = "test-large-switch"
    store.record(
        {
            "_id": "large-switch-source",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": key,
            "runtime": "claude-code",
            "cwd": str(tmp_path),
            "launched": True,
            "test": True,
        }
    )
    store.set_meta(key, name="Keep this card", group="Test", notes="Keep owner notes")
    prepared = {
        "runtime": "claude-code",
        "native_id": "original-conversation",
        "command": "claude",
        "model": "original-model",
        "cwd": str(tmp_path),
        "test": True,
        "checkpoint_id": "saved-checkpoint",
        "seed": brief,
    }

    async def scenario():
        try:
            await launch(server, key, "codex", str(fake), "target-model", prepared)
            received = tmp_path / "received.json"
            for _ in range(100):
                if received.exists():
                    break
                await asyncio.sleep(0.02)
            result = json.loads(received.read_text())
            assert result["key"] == key
            assert result["args"][:2] == ["-c", 'model="target-model"']
            assert result["args"][-1].endswith(brief)
            assert "duckterm session self" in result["args"][-1]
            row = store.session(key)
            assert row["runtime"] == "codex"
            assert row["name"] == "Keep this card"
            assert row["notes"] == "Keep owner notes"
            assert (
                server.restarts.read(key)["previous_conversation"]["native_id"]
                == "original-conversation"
            )
            assert server.orchestrator.get(key).running
        finally:
            await server.orchestrator.stop(key)

    try:
        asyncio.run(scenario())
    finally:
        store.close()
