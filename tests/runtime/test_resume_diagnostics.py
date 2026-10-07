"""Resume reports expose readiness, never identities, transcript text or settings."""

import json
import threading

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm import resume_diagnostics
from duckterm.agents.hooks_install import claude_style_build
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import project_slug
from duckterm.server import Server


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("DUCKTERM_SUPPORT_EMAIL", "")
    store = HistoryStore(tmp_path / "history.sqlite")
    yield store
    store.purge_test_sessions()
    store.close()


def seed(history, key, cwd, native_id=None, state="interrupted", runtime="claude-code"):
    event = {
        "_id": key,
        "_ts": 1,
        "event_type": "SessionStart",
        "session_key": key,
        "name": "PRIVATE-NAME",
        "test": True,
        "cwd": str(cwd),
        "runtime": runtime,
        "prompt": "PRIVATE-CONVERSATION",
        "tool_input": "PRIVATE-TOKEN",
    }
    if native_id is not None:
        event["session_id"] = native_id
    history.record(event)
    history.set_state(key, state)


def test_report_checks_all_stopped_sessions_without_exporting_private_data(
    history, tmp_path, monkeypatch
):
    cwd = tmp_path / "project"
    cwd.mkdir()
    seed(history, "private-key-a", cwd, "private-native-a")
    seed(history, "private-key-b", cwd, "")
    seed(history, "private-key-c", cwd, state="stopped")
    seed(history, "private-key-live", cwd, "private-live", state="busy")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    history._conn.execute(
        "UPDATE sessions SET worktree_path=? WHERE session_key='private-key-a'", (str(worktree),)
    )
    history._conn.commit()
    path = tmp_path / ".claude" / "projects" / project_slug(worktree) / "private-native-a.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("PRIVATE-TRANSCRIPT")
    settings = tmp_path / ".claude" / "settings.json"
    settings.write_text(
        json.dumps(
            claude_style_build(
                {"secret": "PRIVATE-SETTING"},
                str(resume_diagnostics.hook_script_path()),
                "claude-code",
            )
        )
    )
    monkeypatch.setattr(resume_diagnostics.shutil, "which", lambda _: "/PRIVATE/jq")
    before = history._conn.total_changes
    data = resume_diagnostics.snapshot(history, "private-key-a")
    result = resume_diagnostics.render(data)
    text = result["text"]
    assert "Interrupted/stopped sessions: 3 (showing 3)" in text
    assert "Session 1 (selected)" in text
    for expected in [
        "native conversation ID: present",
        "native conversation ID: missing",
        "native conversation ID: empty-string",
        "expected transcript exists: yes",
        "directory source: worktree; exists: yes",
        "jq present: yes",
        "claude-code global hooks: configured",
    ]:
        assert expected in text
    assert project_slug(worktree) not in text
    assert "Claude project slug fingerprint: sha256:" in text
    assert history._conn.total_changes == before
    serialized = json.dumps(result)
    for forbidden in [
        "PRIVATE",
        "private-native",
        "private-key",
        str(tmp_path),
        "settings.json",
        ".jsonl",
    ]:
        assert forbidden not in serialized


def test_missing_file_binding_invalid_ids_and_unavailable_configs(history, tmp_path):
    seed(history, "a", tmp_path, "old-id")
    history.set_restart_control("a", {"native_binding": {"native_id": "new-id"}})
    result = resume_diagnostics.render(resume_diagnostics.snapshot(history, None))["text"]
    assert "expected transcript exists: no" in result
    assert "global hooks: not configured" in result
    history.set_restart_control("a", {"native_binding": {"native_id": ""}})
    assert (
        resume_diagnostics.snapshot(history, None)["sessions"][0]["native_id_state"]
        == "empty-string"
    )
    history.set_restart_control("a", {"native_binding": {"native_id": "../../PRIVATE"}})
    result = resume_diagnostics.render(resume_diagnostics.snapshot(history, None))["text"]
    assert "native conversation ID: invalid" in result
    assert "no usable recorded ID" in result
    assert "PRIVATE" not in result
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{PRIVATE-malformed")
    assert resume_diagnostics.hook_status("claude-code", None) == "unknown"


def test_bounds_and_unsupported_harness_are_honest(history, tmp_path, monkeypatch):
    monkeypatch.setattr(resume_diagnostics, "LIMIT", 2)
    for i in range(3):
        seed(history, str(i), tmp_path / "missing", "native-id", runtime="generic")
    data = resume_diagnostics.snapshot(history, "2")
    assert data["total"] == 3 and len(data["sessions"]) == 2
    assert data["sessions"][0]["selected"]
    text = resume_diagnostics.render(data)["text"]
    assert "showing 2" in text
    assert "exists: no" in text
    assert "no file-per-conversation lookup" in text
    assert "hooks (global/project): not applicable" in text


def test_endpoint_keeps_database_on_owner_thread_and_files_off_loop(history, tmp_path, monkeypatch):
    seed(history, "a", tmp_path)
    server = Server(history=history)
    thread = threading.get_ident()
    original_snapshot = resume_diagnostics.snapshot
    original_render = resume_diagnostics.render

    def snapshot(*args):
        assert threading.get_ident() == thread
        return original_snapshot(*args)

    def render(data):
        assert threading.get_ident() != thread
        return original_render(data)

    monkeypatch.setattr(resume_diagnostics, "snapshot", snapshot)
    monkeypatch.setattr(resume_diagnostics, "render", render)
    try:
        assert dispatch(server, "GET", "/bugreport/context", {})[0] == 401
        status, result = dispatch(
            server, "GET", "/bugreport/context", {"x-duckterm-token": server.token}
        )
        assert status == 200
        item = next(i for i in result["items"] if i["id"] == "resume-readiness")
        assert "native conversation ID: missing" in item["text"]
    finally:
        server.digests.close()
