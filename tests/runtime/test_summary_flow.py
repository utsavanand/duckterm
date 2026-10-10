"""Exit uses the shared progress writer; failed providers never invent an outcome."""

import asyncio
import json
import sys
from pathlib import Path

from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server

FAKE_AGENT = Path(__file__).parent.parent / "fakes" / "fake_agent.py"


def _run_session(store, tmp_path, intention):
    server = Server(history=store)
    agent = GenericRuntime(f"{sys.executable} {FAKE_AGENT}")

    async def scenario():
        finished = asyncio.Event()
        original = server._refresh_progress

        async def refresh(key):
            try:
                await original(key)
            finally:
                finished.set()

        server._refresh_progress = refresh
        key = await server.orchestrator.launch(
            runtime=agent,
            cwd=str(tmp_path),
            prompt=intention,
            test=True,
        )
        try:
            await asyncio.wait_for(server.orchestrator.get(key)._task, 5)
            await asyncio.wait_for(finished.wait(), 5)
            row = store.session(key)
            revision = server.digests.revision(
                key, json.loads(row["progress"] or "{}").get("revision_id")
            )
            return row, revision
        finally:
            await server.orchestrator.stop(key)

    return asyncio.run(scenario())


def test_intention_retained_without_inventing_mechanical_outcome(tmp_path, monkeypatch):
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_CMD", raising=False)
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_URL", raising=False)
    store = HistoryStore(tmp_path / "db.sqlite")
    try:
        row, revision = _run_session(store, tmp_path, "add a logout button")
        assert row["intention"] == "add a logout button"
        assert row["outcome_summary"] is None and revision is None
    finally:
        store.purge_test_sessions()
        store.close()


def test_cli_exit_summary_uses_progress_revision(tmp_path, monkeypatch):
    script = tmp_path / "summary.py"
    script.write_text(
        "import json,sys\n"
        "prompt=sys.stdin.read()\n"
        "print(json.dumps({'accept':[],'done_next_action_ids':[],"
        "'summary_validation':{'ready':True,'reason_codes':[]}} "
        "if 'validating a candidate' in prompt else {'summary':'session summarized by cli'}))\n"
    )
    import shlex

    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", shlex.join([sys.executable, str(script)]))
    store = HistoryStore(tmp_path / "db.sqlite")
    try:
        row, revision = _run_session(store, tmp_path, "refactor parser")
        assert row["outcome_summary"] == "session summarized by cli"
        assert revision["summary"] == row["outcome_summary"]
        # A generic terminal excerpt is useful progress, not a complete native handoff.
        assert not revision["summary_validation"]["ready"]
    finally:
        store.purge_test_sessions()
        store.close()
