"""Exit/turn completion is free; explicit refresh preserves provider failure semantics."""

import asyncio
import json
import sys
from pathlib import Path

from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server

FAKE_AGENT = Path(__file__).parent.parent / "fakes" / "fake_agent.py"


def _run_session(store, tmp_path, intention, monkeypatch):
    import duckterm.server as server_module

    model_calls = []
    summarize = server_module.summarize

    def observed(prompt):
        model_calls.append(prompt)
        return summarize(prompt)

    monkeypatch.setattr(server_module, "summarize", observed)
    server = Server(history=store)
    agent = GenericRuntime(f"{sys.executable} {FAKE_AGENT}")

    async def scenario():
        refreshes = []
        original = server._refresh_progress

        async def refresh(key):
            refreshes.append(key)
            await original(key)

        server._refresh_progress = refresh
        key = await server.orchestrator.launch(
            runtime=agent,
            cwd=str(tmp_path),
            prompt=intention,
            test=True,
        )
        try:
            await asyncio.wait_for(server.orchestrator.get(key)._task, 5)
            server.bus.publish({"event_type": "Stop", "session_key": key, "test": True})
            await asyncio.sleep(0)
            assert refreshes == [] and model_calls == []
            assert store.session(key)["outcome_summary"] is None
            await server._refresh_progress(key)
            assert refreshes == [key]
            assert model_calls  # This test actually exercises the explicit writer.
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
        row, revision = _run_session(store, tmp_path, "add a logout button", monkeypatch)
        assert row["intention"] == "add a logout button"
        assert row["outcome_summary"] is None and revision is None
    finally:
        store.purge_test_sessions()
        store.close()


def test_explicit_cli_summary_uses_progress_revision(tmp_path, monkeypatch):
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
        row, revision = _run_session(store, tmp_path, "refactor parser", monkeypatch)
        assert row["outcome_summary"] == "session summarized by cli"
        assert revision["summary"] == row["outcome_summary"]
        # A generic terminal excerpt is useful progress, not a complete native handoff.
        assert not revision["summary_validation"]["ready"]
    finally:
        store.purge_test_sessions()
        store.close()


def test_failed_explicit_provider_does_not_invent_outcome(tmp_path, monkeypatch):
    import shlex

    script = tmp_path / "fail_summary.py"
    script.write_text("raise SystemExit(2)\n")
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", shlex.join([sys.executable, str(script)]))
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_URL", raising=False)
    store = HistoryStore(tmp_path / "db.sqlite")
    try:
        row, revision = _run_session(store, tmp_path, "keep failed-provider intent", monkeypatch)
        assert row["intention"] == "keep failed-provider intent"
        assert row["outcome_summary"] is None and revision is None
    finally:
        store.purge_test_sessions()
        store.close()
