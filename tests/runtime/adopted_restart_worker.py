"""Two separate server lifetimes for the real-pane adoption regression.

The synthetic CLI only supplies a stable prompt and records argv. Runtime
construction, SQLite identity, tmux adoption, restart and transcript parsing
all use production code. No real agent inference or user state is involved.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import ClaudeCodeRuntime, project_slug
from duckterm.server import Server

root = Path(os.environ["HOME"])
key = "qa215-synthetic"
sid = "11111111-2222-4333-8444-555555555555"
cwd = root / "project"
cwd.mkdir(exist_ok=True)


async def run():
    store = HistoryStore(root / "db.sqlite")
    s = Server(history=store)
    s._maybe_refresh_progress = lambda key: None
    s._relay_observe = lambda event: None
    if sys.argv[1] == "seed":
        path = root / ".claude/projects" / project_slug(cwd) / (sid + ".jsonl")
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "uuid": "qa-message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "QA215 preserved conversation"}],
                    },
                }
            )
            + "\n"
        )
        await s.orchestrator.launch(
            runtime=ClaudeCodeRuntime(str(root / "bin/claude")),
            cwd=str(cwd),
            session_key=key,
            test=True,
        )
        s.bus.publish(
            {
                "event_type": "SessionStart",
                "session_key": key,
                "session_id": sid,
                "runtime": "claude-code",
                "test": True,
            }
        )
        await asyncio.sleep(0.5)
        assert s.orchestrator.get(key).running
        print("seeded real pane", flush=True)
        store.close()
        os._exit(0)
    adopted = await s.orchestrator.reconcile()
    assert key in adopted
    sup = s.orchestrator.get(key)
    print("adopted runtime", sup.runtime.name, flush=True)
    s.bus.publish(
        {
            "event_type": "Stop",
            "session_key": key,
            "session_id": sid,
            "hook_event": True,
            "stop_hook_active": False,
            "test": True,
        }
    )
    await asyncio.sleep(0.2)
    print("screen", repr(sup.visible_screen()), flush=True)
    try:
        await s.restarts.request(key, "qa-model-two")
        for _ in range(100):
            if s.restarts.read(key).get("status") in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)
        result = s.restarts.read(key)
        row = store.session(key)
        messages = s._session_messages(key)
        print(
            json.dumps(
                {
                    "restart": result,
                    "runtime": row["runtime"],
                    "native_id": store.session_id_for(key),
                    "messages": messages,
                }
            ),
            flush=True,
        )
        assert result["status"] == "completed", result
        assert row["runtime"] == "claude-code" and store.session_id_for(key) == sid
        assert s.orchestrator.get(key).running
        assert "QA215 preserved conversation" in json.dumps(messages)
        args = [json.loads(line) for line in (root / "argv.jsonl").read_text().splitlines()]
        assert any(
            "--resume" in argv and sid in argv and "qa-model-two" in argv for argv in args
        ), args

    finally:
        await s.orchestrator.stop(key)
        store.delete_session(key)
        store.close()


asyncio.run(run())
