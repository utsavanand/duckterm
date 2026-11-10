"""Captured source identities and prompt bounds do not depend on provider behavior."""

import asyncio
import json
import shlex
from types import SimpleNamespace

import pytest
from tests.runtime import test_ducky_analysis as fixtures
from tests.runtime.test_ducky_analysis import session

from duckterm import memory_provider
from duckterm.core import fleet_questions as fleet
from duckterm.core.fleet_questions import summarize as phone_summary
from duckterm.core.session_api import APIError
from duckterm.llm import backend
from duckterm.llm.summarizer import Summary


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


@pytest.mark.parametrize("available", [False, True])
@pytest.mark.parametrize("command_override", [False, True])
def test_phone_jobs_never_execute_legacy_command_or_copilot(
    app, monkeypatch, available, command_override
):
    session(app)
    device = fixtures.phone(app)
    binaries = app.root / "bin"
    binaries.mkdir()
    invoked = app.root / "legacy-command-invoked"
    script = "#!/bin/sh\nprintf invoked > " + shlex.quote(str(invoked)) + "\nprintf unsafe"
    for name in ("legacy-summary", "copilot", *(("claude",) if available else ())):
        executable = binaries / name
        executable.write_text(script)
        executable.chmod(0o700)
    # Only owned canaries are executable; no installed harness, settings,
    # credential or provider can be reached even by the old unsafe fallback.
    monkeypatch.setenv("PATH", str(binaries))
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_URL", raising=False)
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_CMD", raising=False)
    if command_override:
        monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", shlex.quote(str(binaries / "legacy-summary")))
    monkeypatch.setattr(
        backend.ai_settings,
        "load",
        lambda: {"backend": "harness", "harness": "auto", "harness_deep_model": "fixture-deep"},
    )
    # Restore the real phone provider boundary hidden by the common fixture.
    monkeypatch.setattr(fleet, "summarize", phone_summary)
    completions, harness_calls = [], []
    complete = backend.complete

    async def recorded_complete(prompt, **kwargs):
        completions.append((prompt, kwargs))
        return await complete(prompt, **kwargs)

    async def text_provider(name, model, prompt, schema, **kwargs):
        harness_calls.append((name, model, prompt, schema, kwargs))
        return "  Safe backend answer  "

    monkeypatch.setattr(backend, "complete", recorded_complete)
    monkeypatch.setattr(memory_provider, "generate", text_provider)

    async def scenario():
        payload, accepted = await fixtures.ask(app, device)
        assert accepted["receipt"]["status"] == "queued"
        await fixtures.settled(app.service)
        receipt = (await app.service.lookup(device, payload["request_id"]))["receipt"]
        assert not invoked.exists(), "Phone analysis executed an unrestricted legacy command"
        if available:
            assert receipt["status"] == "complete"
            assert receipt["answer"] == "Safe backend answer"
            assert len(completions) == len(harness_calls) == 1
            prompt, options = completions[0]
            assert options["tier"] == "deep" and "Output of a" in prompt
            assert harness_calls == [
                (
                    "claude-code",
                    "fixture-deep",
                    prompt,
                    None,
                    {"user_settings": True, "effort": "medium"},
                )
            ]
        else:
            assert receipt["status"] == "failed" and receipt["reason"] == "provider_unavailable"
            assert receipt["answer"] is None
            assert completions == harness_calls == []

    fixtures.run(app, scenario)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (memory_provider.BatchTimeout(), "provider_timeout"),
        (APIError(503, "private"), "provider_failed"),
    ],
)
def test_backend_errors_remain_sanitized_summary_results(monkeypatch, error, reason):
    monkeypatch.setattr(backend, "ready", lambda: True)

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(backend, "complete", fail)
    assert fleet.generate(fleet.Prepared("Fixture prompt", ("a",))) == Summary("", "none", reason)


def test_unexpected_backend_error_never_enters_phone_receipt(app, monkeypatch):
    session(app)
    device = fixtures.phone(app)
    monkeypatch.setattr(fleet, "summarize", phone_summary)
    monkeypatch.setattr(backend, "ready", lambda: True)

    async def fail(*args, **kwargs):
        raise RuntimeError("private fixture provider diagnostic")

    monkeypatch.setattr(backend, "complete", fail)

    async def scenario():
        payload, _ = await fixtures.ask(app, device)
        await fixtures.settled(app.service)
        result = await app.service.lookup(device, payload["request_id"])
        assert result["receipt"]["status"] == "failed"
        assert result["receipt"]["reason"] == "provider_failed"
        assert "private fixture" not in json.dumps(result)
        assert not fixtures.raw(app, device, payload)["answer"]

    fixtures.run(app, scenario)


def test_empty_answer_and_explicit_desktop_provider_keep_their_contract(monkeypatch):
    def unavailable():
        pytest.fail("Empty answers and explicit desktop providers must not probe phone AI")

    monkeypatch.setattr(backend, "ready", unavailable)
    assert fleet.generate(fleet.Prepared("", (), empty_answer="No sessions.")) == Summary(
        "No sessions.", "none"
    )
    calls = []
    expected = Summary("Explicit provider answer", "fixture")

    def provider(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return expected

    assert fleet.generate(fleet.Prepared("Fixture prompt", ("a",)), provider) is expected
    assert calls == [("Fixture prompt", {"tier": "deep"})]
