"""Context occupancy is neither lifetime spend nor a transcript from another session."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.runtime.test_session_api import dispatch
from tests.unit.test_context_evidence import codex_usage, meta, write

from duckterm import context_evidence
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def test_sessions_reports_codex_latest_usage_capacity_and_persists_model(tmp_path, monkeypatch):
    home = tmp_path / "home"
    logs = home / ".codex/sessions/2026/10/28"
    logs.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("DUCKTERM_ORACLE", "off")
    path = write(
        logs / "rollout-2026-10-28-native.jsonl",
        [
            meta(),
            {"type": "turn_context", "payload": {"model": "gpt-6-astra"}},
            codex_usage(202390),
        ],
    )
    history = HistoryStore(tmp_path / "history.sqlite")
    try:
        for key, sid in (("known", "native"), ("missing", None)):
            history.record(
                {
                    "_id": key,
                    "_ts": 1,
                    "event_type": "SessionStart",
                    "session_key": key,
                    "session_id": sid,
                    "runtime": "codex",
                    "cwd": str(tmp_path),
                    "test": True,
                }
            )
        server = Server(history=history)
        auth = {"x-duckterm-token": server.token}
        status, data = dispatch(server, "GET", "/sessions", auth)
        assert status == 200
        rows = {row["session_key"]: row for row in data["sessions"]}
        assert rows["known"]["context_tokens"] == 202390
        assert rows["known"]["context_window_tokens"] == 258400
        assert rows["known"]["model"] == "gpt-6-astra"
        assert history.session("known")["model"] == "gpt-6-astra"
        assert rows["missing"]["context_tokens"] is None
        assert rows["missing"]["model"] is None
        with path.open("a") as stream:
            stream.write(json.dumps(codex_usage(12345)) + "\n")
        _, data = dispatch(server, "GET", "/sessions", auth)
        assert (
            next(row for row in data["sessions"] if row["session_key"] == "known")["context_tokens"]
            == 12345
        )
        path.unlink()
        _, data = dispatch(server, "GET", "/sessions", auth)
        row = next(row for row in data["sessions"] if row["session_key"] == "known")
        assert row["context_tokens"] is None
        assert row["model"] == "gpt-6-astra"
    finally:
        history.close()


@pytest.mark.parametrize("change", ["identity", "cwd", "runtime", "restart_json", "deleted"])
def test_codex_read_runs_off_loop_and_discards_changed_binding(monkeypatch, change):
    identity = {"native_id": "native", "status": "recorded"}
    row = {"session_key": "test", "runtime": "codex", "cwd": "/tmp/test"}
    captured = dict(row)
    loop_thread = threading.get_ident()

    def project(_row, _identity):
        assert threading.get_ident() != loop_thread
        if change == "identity":
            identity["native_id"] = "new"
        elif change == "deleted":
            row.clear()
        else:
            row[change] = "changed"
        return {"context_tokens": 202390, "model": "gpt-6-astra"}

    monkeypatch.setattr(context_evidence, "project", project)
    server = SimpleNamespace(
        history=SimpleNamespace(
            native_identity=lambda key: dict(identity), session=lambda key: dict(row) or None
        )
    )
    assert asyncio.run(Server._transcript_stats_for(server, captured)) == {}
