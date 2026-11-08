"""Captured source identities and prompt bounds do not depend on provider behavior."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from tests.runtime import test_ducky_analysis as fixtures
from tests.runtime.test_ducky_analysis import session

from duckterm.core import fleet_questions as fleet


@pytest.fixture(name="app")
def analysis_fixture(tmp_path, monkeypatch):
    yield from fixtures.app.__wrapped__(tmp_path, monkeypatch)


def test_membership_subtree_ungrouped_hidden_and_native_generation(app):
    session(app, "a", "Work")
    session(app, "b", "Work/Sub")
    session(app, "c", "WorkSibling")
    session(app, "d", "")
    session(app, "hidden", "Work", visible=False)
    folder = fleet.capture(app.server, fleet.Scope("folder", "Work"))
    assert [row["session_key"] for row in folder.rows] == ["a", "b"]
    assert [
        row["session_key"] for row in fleet.capture(app.server, fleet.Scope("ungrouped")).rows
    ] == ["d"]
    assert len(fleet.capture(app.server, fleet.Scope("oracle")).rows) == 4
    app.server.history._conn.execute(
        "UPDATE sessions SET restart_json=? WHERE session_key='a'",
        (json.dumps({"native_binding": {"generation": "replacement"}}),),
    )
    app.server.history._conn.commit()
    assert not fleet.current(app.server, folder)


def test_terminal_process_replacement_and_inplace_fd_change_invalidate_capture(app):
    supervisor = session(app)
    captured = fleet.capture(app.server, fleet.Scope("oracle"))
    supervisor._primary_fd = 3
    assert not fleet.current(app.server, captured)
    captured = fleet.capture(app.server, fleet.Scope("oracle"))
    supervisor._proc = SimpleNamespace(pid=42)
    assert not fleet.current(app.server, captured)


def test_all_prompt_limits_preserve_question_and_multibyte_boundaries(app):
    # A long valid folder path makes all per-source limits together exceed the
    # aggregate limit, so the engine must clip digests without clipping the ask.
    folder = "é" * 2000
    app.server.history.create_folder(folder)
    for number in range(42):
        supervisor = session(app, f"a{number:02}", folder)
        supervisor.screen_text = lambda lines: "界" * 6000
    captured = fleet.capture(app.server, fleet.Scope("folder", folder))
    question = "Q" * 4096
    prior = [{"q": "P" * 4096, "a": "A" * 32768}] * 2
    prepared = asyncio.run(fleet.prepare(app.server, captured, question, prior, lambda: None))
    assert len(prepared.sessions) == 40 and prepared.truncated
    assert len(prepared.prompt.encode()) <= 256 * 1024
    assert prepared.prompt.endswith("Question: " + question + "\nAnswer:")
    assert "Output of a40" not in prepared.prompt
    assert "\ufffd" not in prepared.prompt


def test_folder_delete_recreate_never_reuses_scope_identity(app):
    scope = fleet.Scope("folder", "Empty")
    before = fleet.binding(app.server, scope)
    app.server.history.folder_chats.change(
        "Empty", None, lambda: app.server.history.delete_folder("Empty")
    )
    app.server.history.create_folder("Empty")
    after = fleet.binding(app.server, scope)
    assert before != after
