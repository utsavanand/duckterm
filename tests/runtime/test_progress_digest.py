"""The running progress digest: Stop events trigger a debounced summarizer
call whose parsed digest lands on the session row (and therefore in
/sessions). The summarizer is a stub script so no real agent runs."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

_DIGEST = {
    "summary": "Standing up the demo app.",
    "deliverables": ["scaffolded the app"],
    "learnings": ["sqlite is enough"],
    "next_actions": ["wire login"],
}


@pytest.fixture()
def fake_summarizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake-llm.sh"
    script.write_text(f"#!/bin/sh\ncat >/dev/null\nprintf '%s' '{json.dumps(_DIGEST)}'\n")
    script.chmod(0o755)
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", str(script))
    return script


def _server() -> Server:
    return Server(history=HistoryStore(Path(tempfile.mkdtemp()) / "db.sqlite"))


def test_refresh_progress_stores_digest_on_session(fake_summarizer: Path) -> None:
    async def scenario() -> dict:
        server = _server()
        server.bus.publish({"event_type": "SessionStart", "session_key": "S", "cwd": "/tmp"})
        from duckterm.runtimes.generic import GenericRuntime

        await server.orchestrator.launch(
            runtime=GenericRuntime("sh -c 'echo WORKING_ON_APP; exec cat'"),
            cwd="/tmp",
            session_key="S",
        )
        await asyncio.sleep(0.3)  # let the pty produce a screen
        await server._refresh_progress("S")
        await server.orchestrator.stop("S")
        row = server.history.session("S")
        assert row is not None
        return json.loads(row["progress"])

    digest = asyncio.run(scenario())
    assert digest == {**_DIGEST, "user_learnings": []}  # parse fills the new bucket


def test_stop_event_triggers_debounced_refresh(fake_summarizer: Path) -> None:
    server = _server()
    for i in range(5):
        server.bus.publish(
            {"event_type": "PreToolUse", "session_key": "S", "cwd": "/tmp", "_ts": i}
        )
    # No running loop here, so the trigger can't schedule — but it must have
    # recorded its debounce mark only when the gate passed.
    server.bus.publish({"event_type": "Stop", "session_key": "S"})
    assert "S" in server._progress_marks  # gate passed (5+ events, first run)

    marks_before = dict(server._progress_marks)
    server.bus.publish({"event_type": "Stop", "session_key": "S"})
    # Within the debounce window with no new events: no new mark taken.
    assert server._progress_marks == marks_before
