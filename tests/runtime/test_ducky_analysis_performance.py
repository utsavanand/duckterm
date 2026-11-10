"""Analysis permission fences stay fresh without monopolizing the owner loop."""

import asyncio
import json
import threading

import pytest
from tests.runtime import test_ducky_analysis as fixtures
from tests.runtime.test_ducky_analysis import ask, phone, raw, run, session, settled

from duckterm.core import fleet_questions as fleet
from duckterm.llm.summarizer import Summary
from duckterm.mobile import analysis as analysis_module
from duckterm.mobile import grants as grant_module
from duckterm.mobile.analysis import AnalysisError
from duckterm.mobile.grants import Grants
from duckterm.persistence.history import HistoryStore


@pytest.fixture(name="app")
def analysis_fixture(tmp_path, monkeypatch):
    yield from fixtures.app.__wrapped__(tmp_path, monkeypatch)


def test_binding_keeps_capture_identity_without_metrics_or_full_session_reads(app, monkeypatch):
    for index in range(400):
        session(app, f"a{index}", "Work" if index < 20 else "Elsewhere")
    # Scope already exists in the explicit folder catalog. Avoid the fixture's
    # synthetic test-folder discovery reads when measuring the production path.
    monkeypatch.setattr(
        app.server.history,
        "folders",
        lambda **kwargs: HistoryStore.folders(app.server.history, **kwargs),
    )
    scope = fleet.Scope("folder", "Work")
    captured = fleet.capture(app.server, scope)

    def forbidden(*args, **kwargs):
        raise AssertionError("A binding must not expand full session rows or metrics")

    monkeypatch.setattr(app.server.history, "sessions", forbidden)
    monkeypatch.setattr(app.server.history, "session", forbidden)
    monkeypatch.setattr(app.server.history, "metrics", forbidden)
    queries = []
    app.server.history._conn.set_trace_callback(queries.append)
    try:
        assert fleet.binding(app.server, scope) == captured.binding
    finally:
        app.server.history._conn.set_trace_callback(None)
    session_queries = [sql for sql in queries if "FROM sessions" in sql]
    assert all("metrics" not in sql and "SELECT *" not in sql for sql in queries)
    assert len([sql for sql in session_queries if "restart_json" in sql]) == 1
    assert any("grp = 'Work'" in sql and "substr(grp" in sql for sql in session_queries)
    assert fleet.current(app.server, captured)
    app.server.orchestrator._supervisors["a0"]._primary_fd = 7
    assert not fleet.current(app.server, captured)


def test_analysis_query_uses_literal_case_sensitive_subtrees_and_excludes_hidden_rows(app):
    folders = ["Work_%'é", "Work_%'é/Sub", "WorkXXé", "work_%'é", "Work_%'éSibling", ""]
    for index, folder in enumerate(folders):
        session(app, f"a{index}", folder)
    session(app, "archived", folders[0], state="archived")
    session(app, "merged", folders[0], state="merged")
    history = app.server.history
    # All real fixture rows remain test:true. Production's default must hide all.
    assert HistoryStore.analysis_sessions(history, folders[0]) == []
    rows = HistoryStore.analysis_sessions(history, folders[0], include_test=True)
    assert [row["session_key"] for row in rows] == ["a0", "a1"]
    ungrouped = HistoryStore.analysis_sessions(history, ungrouped=True, include_test=True)
    assert [row["session_key"] for row in ungrouped] == ["a5"]
    assert "archived" not in {
        row["session_key"] for row in HistoryStore.analysis_sessions(history, include_test=True)
    }


def test_binding_preserves_legacy_native_identity_and_replacement_boundary(app):
    session(app)
    history = app.server.history
    history.record(
        {
            "_id": "legacy-identity",
            "_ts": 3,
            "event_type": "Notification",
            "session_key": "a",
            "runtime": "codex",
            "session_id": "legacy-native-id",
            "test": True,
        }
    )
    history._conn.execute("UPDATE sessions SET restart_json='{}' WHERE session_key='a'")
    history._conn.commit()
    captured = fleet.capture(app.server, fleet.Scope("oracle"))
    assert history.native_identity("a")["native_id"] == "legacy-native-id"
    assert fleet.binding(app.server, captured.scope) == captured.binding
    history._conn.execute(
        "UPDATE sessions SET restart_json=? WHERE session_key='a'",
        (json.dumps({"native_binding": {"runtime": "codex", "generation": "new"}}),),
    )
    history._conn.commit()
    assert history.native_identity("a")["status"] == "pending"
    assert not fleet.current(app.server, captured)


def test_check_computes_one_binding_and_still_checks_captured_supervisor(app, monkeypatch):
    supervisor = session(app)
    device = phone(app)

    async def scenario():
        value = await app.service.context(device, {"scope": {"kind": "folder", "path": "Work"}})
        row = app.service.contexts[value["context_id"]].row
        capture = fleet.capture(app.server, fleet.Scope("folder", "Work"))
        binding = fleet.binding
        calls = []

        def counted(*args):
            calls.append(1)
            return binding(*args)

        monkeypatch.setattr(fleet, "binding", counted)
        app.service._check(row, capture)
        assert len(calls) == 1
        calls.clear()
        with app.service._authorized(device, capture.scope, row["revision"]):
            app.service._check_identity(row, capture)
        assert len(calls) == 1
        supervisor._primary_fd = 7
        with pytest.raises(AnalysisError, match="scope_changed"):
            app.service._check(row, capture)
        assert len(calls) == 2

    run(app, scenario)


def test_contended_grant_lock_never_sleeps_on_event_loop(app, monkeypatch):
    device = phone(app)

    def forbidden_sleep(*args):
        raise AssertionError("Permission checks cannot sleep on the event loop")

    async def scenario():
        with Grants(app.root).locked(), monkeypatch.context() as patch:
            patch.setattr(grant_module.time, "sleep", forbidden_sleep)
            with pytest.raises(AnalysisError, match="analysis_unavailable"):
                await app.service.context(device, {"scope": {"kind": "oracle"}})
            assert not app.service.contexts
        assert await app.service.context(device, {"scope": {"kind": "oracle"}})

    run(app, scenario)


def test_two_pending_providers_do_not_poll_at_ten_hertz_and_revoke_still_wins(app, monkeypatch):
    session(app)
    devices = [phone(app), phone(app)]
    release = threading.Event()
    entered = []

    def provider(*args, **kwargs):
        entered.append(1)
        assert release.wait(5)
        return Summary("Private answer", "fixture")

    monkeypatch.setattr(fleet, "summarize", provider)

    async def scenario():
        try:
            payloads = [(await ask(app, device))[0] for device in devices]
            async with asyncio.timeout(2):
                while len(entered) != 2:
                    await asyncio.sleep(0.01)
            check = app.service._check_work
            calls = []

            def counted(*args):
                calls.append(1)
                return check(*args)

            monkeypatch.setattr(app.service, "_check_work", counted)
            await asyncio.sleep(0.35)
            assert len(calls) <= 2
            Grants(app.root).revoke(devices[0]["id"])
        finally:
            release.set()
        await settled(app.service)
        denied = raw(app, devices[0], payloads[0])
        assert denied["status"] == "cancelled" and denied["answer"] is None
        assert raw(app, devices[1], payloads[1])["status"] == "complete"

    run(app, scenario)


@pytest.mark.parametrize("deadline", ["monotonic", "wall"])
def test_polling_caps_wait_at_existing_deadline(app, monkeypatch, deadline):
    session(app)
    device = phone(app)
    release = threading.Event()

    def provider(*args, **kwargs):
        assert release.wait(5)
        return Summary("Expired answer", "fixture")

    monkeypatch.setattr(fleet, "summarize", provider)

    async def scenario():
        try:
            payload, _ = await ask(app, device)
            key = (device["host_id"], device["id"], payload["request_id"])
            if deadline == "monotonic":
                app.service.deadlines[key] = asyncio.get_running_loop().time() + 0.15
            else:
                # The row captured by the worker uses the same persisted expiry.
                with app.service.store.connect() as db:
                    db.execute(
                        "UPDATE questions SET expires_at=created_at+150 WHERE request_id=?",
                        (payload["request_id"],),
                    )
            async with asyncio.timeout(0.75):
                while raw(app, device, payload)["status"] != "expired":
                    await asyncio.sleep(0.01)
            assert raw(app, device, payload)["answer"] is None
            assert app.service.tasks  # Real provider capacity stays occupied.
        finally:
            release.set()
        await settled(app.service)

    run(app, scenario)


def test_timeout_inside_authorized_operation_is_never_retried(app):
    device = phone(app)
    calls = []

    def operation():
        with app.service._authorized(device, fleet.Scope("oracle")):
            calls.append(1)
            raise TimeoutError("operation already started")

    async def scenario():
        with pytest.raises(AnalysisError, match="analysis_unavailable"):
            await app.service._retry_grant(operation)
        assert calls == [1]

    run(app, scenario)


@pytest.mark.parametrize("phase", ["pending", "completion", "revocation"])
def test_brief_contention_does_not_discard_paid_answer_and_revocation_still_wins(
    app, monkeypatch, phase
):
    session(app)
    device = phone(app)
    provider_entered, provider_release = threading.Event(), threading.Event()
    held, unlock = threading.Event(), threading.Event()
    monkeypatch.setattr(analysis_module, "CHECK_SECONDS", 0.01)

    def provider(*args, **kwargs):
        provider_entered.set()
        assert provider_release.wait(5)
        return Summary("Paid result", "fixture")

    def holder():
        with Grants(app.root).locked(write=phase == "revocation") as state:
            if phase == "revocation":
                state["devices"][device["id"]]["status"] = "revoked"
            held.set()
            assert unlock.wait(2)

    monkeypatch.setattr(fleet, "summarize", provider)
    thread = threading.Thread(target=holder)

    async def scenario():
        try:
            payload, _ = await ask(app, device)
            assert await asyncio.to_thread(provider_entered.wait, 2)
            thread.start()
            assert await asyncio.to_thread(held.wait, 1)
            if phase != "pending":
                provider_release.set()
            # The owner loop must keep progressing while another thread owns
            # the lock. No speculative authorization is granted during retry.
            for _ in range(3):
                await asyncio.sleep(0.01)
                assert raw(app, device, payload)["answer"] is None
            unlock.set()
            await asyncio.to_thread(thread.join, 1)
            provider_release.set()
            await settled(app.service)
            result = raw(app, device, payload)
            if phase == "revocation":
                assert result["status"] == "cancelled" and result["answer"] is None
            else:
                assert result["status"] == "complete" and result["answer"] == "Paid result"
        finally:
            unlock.set()
            provider_release.set()
            if thread.ident is not None:
                await asyncio.to_thread(thread.join, 1)

    run(app, scenario)
