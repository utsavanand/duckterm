"""Scoped jobs use fake inference, isolated grants, and test:true SQLite fixtures."""

import asyncio
import json
import secrets
import sqlite3
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.runtime.test_ducky import visible_session_fixture

from duckterm.core import fleet_questions as fleet
from duckterm.editions import CODE, FULL, LITE
from duckterm.llm.summarizer import Summary
from duckterm.mobile import analysis as module
from duckterm.mobile.analysis import Analysis, AnalysisError
from duckterm.mobile.grants import Grants
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setenv("DUCKTERM_HOME", str(root))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    monkeypatch.setenv("DUCKTERM_SUMMARIZER", "off")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    (root / "ducky-bridge.json").write_text('{"version":1}')
    history = HistoryStore(root / "state.sqlite", edition=FULL)
    server = Server(history=history, edition=FULL)
    history.create_folder("Work/Sub")
    history.create_folder("Empty")
    visible = visible_session_fixture(server, monkeypatch, set())

    async def stats(row):
        return {"model": "fixture"}

    monkeypatch.setattr(server, "_transcript_stats_for", stats)

    def forbidden(*args, **kwargs):
        raise AssertionError("No real model may run in analysis tests")

    monkeypatch.setattr(fleet, "summarize", forbidden)
    service = Analysis(server, root)
    yield SimpleNamespace(server=server, root=root, service=service, visible=visible)
    assert all(row[0] == 1 for row in history._conn.execute("SELECT test FROM sessions"))
    history.purge_test_sessions()
    server.digests.close()
    history.close()


def phone(app, capabilities=("analysis:folder", "analysis:oracle")):
    grants = Grants(app.root)
    token = secrets.token_urlsafe(32)
    pending = grants.claim(grants.invite()["invite"], token, "Fixture phone")
    grants.approve(pending["id"], capabilities=list(capabilities))
    return grants.authenticate(token)


def session(app, key="a", folder="Work", *, state="busy", visible=True):
    history = app.server.history
    history.record(
        {
            "_id": "start-" + key,
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": key,
            "runtime": "codex",
            "cwd": str(app.root),
            "test": True,
        }
    )
    history.set_meta(key, group=folder, name="Agent " + key)
    history.set_state(key, state, now=2)
    if visible:
        app.visible.add(key)
    supervisor = SimpleNamespace(
        screen_text=lambda lines: f"Output of {key}",
        _proc=None,
        _primary_fd=None,
        _tmux_target="fixture:" + key,
    )
    app.server.orchestrator._supervisors[key] = supervisor
    return supervisor


def run(app, scenario):
    async def wrapped():
        try:
            return await scenario()
        finally:
            await app.service.close()

    return asyncio.run(wrapped())


async def ask(app, device, scope=None, text="What needs attention?"):
    scope = scope or {"kind": "folder", "path": "Work"}
    context = await app.service.context(device, {"scope": scope})
    payload = {
        "request_id": str(uuid.uuid4()),
        "context_id": context["context_id"],
        "question": text,
    }
    return payload, await app.service.accept(device, payload)


async def settled(service):
    async with asyncio.timeout(4):
        while service.tasks:
            await asyncio.sleep(0.01)


def raw(app, device, payload):
    return app.service.store.find(
        device["host_id"], device["id"], payload["request_id"], module._now()
    )


def fails(reason, code=None):
    class Check:
        def __enter__(self):
            self.error = pytest.raises(AnalysisError)
            return self.error.__enter__()

        def __exit__(self, *args):
            result = self.error.__exit__(*args)
            assert self.error.excinfo.value.reason == reason
            if code is not None:
                assert self.error.excinfo.value.status == code
            return result

    return Check()


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
@pytest.mark.parametrize("scope", [{"kind": "oracle"}, {"kind": "folder", "path": "Work"}])
@pytest.mark.parametrize("granted", [False, True])
def test_permissions_are_independent_and_intersect_edition(app, edition, scope, granted):
    app.server.edition = edition
    device = phone(app, () if not granted else ("analysis:folder", "analysis:oracle"))
    allowed = granted and edition.includes("oracle" if scope["kind"] == "oracle" else "folder_chat")

    async def scenario():
        if allowed:
            result = await app.service.context(device, {"scope": scope})
            assert result["history"] == [] and result["pending"] is None
        else:
            with fails("permission_required", 403):
                await app.service.context(device, {"scope": scope})

    run(app, scenario)


def test_context_no_inference_and_per_phone_limit(app, monkeypatch):
    device = phone(app)

    async def scenario():
        for _ in range(4):
            value = await app.service.context(device, {"scope": {"kind": "oracle"}})
            assert value["expires_at"] > module._now()
        with fails("context_limit", 429):
            await app.service.context(device, {"scope": {"kind": "oracle"}})
        clock = module._now()
        monkeypatch.setattr(module, "_now", lambda: clock + 60001)
        await app.service.context(device, {"scope": {"kind": "oracle"}})
        assert len(app.service.contexts) == 1

    run(app, scenario)


@pytest.mark.parametrize(
    "scope",
    [
        None,
        {},
        {"kind": "folder", "path": ""},
        {"kind": "folder", "path": "../Work"},
        {"kind": "oracle", "path": "Work"},
        {"kind": "ungrouped", "path": ""},
    ],
)
def test_scopes_are_exact_and_ungrouped_is_not_oracle(app, scope):
    device = phone(app)

    async def scenario():
        with fails("invalid_scope", 400):
            await app.service.context(device, {"scope": scope})

    run(app, scenario)


@pytest.mark.parametrize(
    ("field", "value", "reason", "code"),
    [
        ("request_id", "not-uuid", "invalid_request_id", 400),
        ("request_id", str(uuid.uuid4()).upper(), "invalid_request_id", 400),
        ("context_id", "../unsafe", "invalid_context", 400),
        ("question", " \n", "invalid_question", 400),
        ("question", "secret\x85", "invalid_question", 400),
        ("question", "\ud800", "invalid_question", 400),
        ("question", "é" * 2049, "question_too_large", 413),
    ],
)
def test_question_validation_is_bounded_before_storage(app, field, value, reason, code):
    device = phone(app)

    async def scenario():
        request = {"request_id": str(uuid.uuid4()), "context_id": "safe", "question": "Question"}
        request[field] = value
        with fails(reason, code):
            await app.service.accept(device, request)
        assert not app.service.store.path.exists()

    run(app, scenario)


def test_empty_scope_is_deterministic_replay_and_private_history(app, monkeypatch):
    device, other = phone(app), phone(app)

    async def scenario():
        payload, accepted = await ask(app, device, {"kind": "folder", "path": "Empty"})
        assert accepted["receipt"]["status"] == "queued"
        await settled(app.service)
        complete = await app.service.lookup(device, payload["request_id"])
        receipt = complete["receipt"]
        assert receipt["status"] == "complete" and receipt["source_count"] == 0
        assert receipt["answer"] == "No sessions are running in Empty."
        assert receipt["captured_at"] is not None and receipt["reason"] is None
        assert receipt["scope"] == {"kind": "folder", "path": "Empty"}
        with fails("request_not_found", 404):
            await app.service.lookup(other, payload["request_id"])
        clock = module._now()
        monkeypatch.setattr(module, "_now", lambda: clock + 61000)
        assert await app.service.accept(device, payload) == complete
        with fails("request_conflict", 409):
            await app.service.accept(device, {**payload, "question": "Changed"})
        history = await app.service.context(device, {"scope": {"kind": "folder", "path": "Empty"}})
        assert history["history"] == [receipt]
        private = await app.service.context(other, {"scope": {"kind": "folder", "path": "Empty"}})
        assert private["history"] == []
        assert app.server.history.folder_chats.snapshot("Empty")[1] == []
        assert app.service.store.root.stat().st_mode & 0o777 == 0o700
        assert app.service.store.path.stat().st_mode & 0o777 == 0o600

    run(app, scenario)


def test_context_stale_and_body_conflict_do_not_start_a_job(app, monkeypatch):
    device = phone(app)

    async def scenario():
        context = await app.service.context(device, {"scope": {"kind": "oracle"}})
        clock = module._now()
        monkeypatch.setattr(module, "_now", lambda: clock + 60001)
        with fails("stale_context", 409):
            await app.service.accept(
                device,
                {
                    "request_id": str(uuid.uuid4()),
                    "context_id": context["context_id"],
                    "question": "Question",
                },
            )
        assert app.service.store.queued(module._now()) == []

    run(app, scenario)


def test_provider_receives_only_scope_and_own_history_and_deep_tier(app, monkeypatch):
    for key, folder in [
        ("a", "Work"),
        ("child", "Work/Sub"),
        ("sibling", "WorkOther"),
        ("loose", ""),
    ]:
        session(app, key, folder)
    session(app, "hidden", "Work", visible=False)
    session(app, "archived", "Work", state="archived")
    session(app, "rest", "Work", state="stopped")
    prompts = []

    def provider(prompt, **kwargs):
        assert kwargs == {"tier": "deep"}
        prompts.append(prompt)
        return Summary("Answer from fake provider", "fixture")

    monkeypatch.setattr(fleet, "summarize", provider)
    device = phone(app)

    async def scenario():
        payload, _ = await ask(app, device)
        await settled(app.service)
        result = await app.service.lookup(device, payload["request_id"])
        assert result["receipt"]["status"] == "complete"
        assert result["receipt"]["source_count"] == 2
        assert "Output of a" in prompts[0] and "Output of child" in prompts[0]
        assert all(
            "Output of " + key not in prompts[0]
            for key in ("sibling", "loose", "hidden", "archived", "rest")
        )
        await ask(app, device, {"kind": "ungrouped"})
        await settled(app.service)
        assert "Output of loose" in prompts[1] and "Output of a" not in prompts[1]
        assert "Answer from fake provider" not in prompts[1]
        await ask(app, device)
        await settled(app.service)
        assert "Earlier exchanges:" in prompts[2]
        assert "Answer from fake provider" in prompts[2]

    run(app, scenario)


@pytest.mark.parametrize(
    "change", ["membership", "generation", "config", "certificate", "revision"]
)
def test_completed_answer_is_fenced_on_every_read(app, monkeypatch, change):
    session(app)
    monkeypatch.setattr(fleet, "summarize", lambda *a, **k: Summary("Private answer", "fixture"))
    device = phone(app)

    async def scenario():
        payload, _ = await ask(app, device)
        await settled(app.service)
        assert raw(app, device, payload)["status"] == "complete"
        if change == "membership":
            app.server.history.set_meta("a", group="Work/Sub")
        elif change == "generation":
            app.server.history._conn.execute(
                "UPDATE sessions SET restart_json=? WHERE session_key='a'",
                (json.dumps({"native_binding": {"generation": "replacement"}}),),
            )
            app.server.history._conn.commit()
        elif change in {"config", "certificate"}:
            name = "ducky-bridge.json" if change == "config" else "ducky-cert.pem"
            (app.root / name).write_text("Changed configuration fixture")
        else:
            with Grants(app.root).locked(write=True) as state:
                state["devices"][device["id"]]["authorization_revision"] += 1
        with pytest.raises(AnalysisError):
            await app.service.lookup(device, payload["request_id"])
        with pytest.raises(AnalysisError):
            await app.service.accept(device, payload)

    run(app, scenario)


@pytest.mark.parametrize("change", ["move", "replace", "inplace", "revoke"])
def test_change_while_digest_awaits_prevents_inference(app, monkeypatch, change):
    supervisor = session(app)
    device = phone(app)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def stats(row):
            entered.set()
            await release.wait()
            return {}

        monkeypatch.setattr(app.server, "_transcript_stats_for", stats)
        payload, _ = await ask(app, device)
        await asyncio.wait_for(entered.wait(), 2)
        if change == "move":
            app.server.history.set_meta("a", group="Elsewhere")
        elif change == "replace":
            app.server.orchestrator._supervisors["a"] = SimpleNamespace(screen_text=lambda n: "NEW")
        elif change == "inplace":
            supervisor._tmux_target = "replacement"
        else:
            Grants(app.root).revoke(device["id"])
        release.set()
        await settled(app.service)
        result = raw(app, device, payload)
        assert result["status"] == "cancelled" and result["answer"] is None
        assert result["reason"] in {"scope_changed", "permission_changed"}

    run(app, scenario)


def test_concurrent_replay_pending_recovery_and_one_active_per_phone(app, monkeypatch):
    session(app)
    device = phone(app)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def provider(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(4)
        return Summary("One answer", "fixture")

    monkeypatch.setattr(fleet, "summarize", provider)

    async def scenario():
        try:
            payload, first = await ask(app, device)
            replay = await asyncio.gather(*(app.service.accept(device, payload) for _ in range(8)))
            assert all(item["receipt"]["request_id"] == payload["request_id"] for item in replay)
            assert await asyncio.to_thread(entered.wait, 2)
            context = await app.service.context(
                device, {"scope": {"kind": "folder", "path": "Work"}}
            )
            assert context["pending"]["context_id"] == first["receipt"]["context_id"]
            assert context["context_id"] != context["pending"]["context_id"]
            with fails("queue_limit", 429):
                await app.service.accept(
                    device,
                    {
                        **payload,
                        "request_id": str(uuid.uuid4()),
                        "context_id": context["context_id"],
                    },
                )
            assert calls == [1]
        finally:
            release.set()
        await settled(app.service)
        assert raw(app, device, payload)["status"] == "complete"

    run(app, scenario)


def test_revoke_during_provider_never_publishes_and_close_does_not_wait_for_thread(
    app, monkeypatch
):
    session(app)
    device = phone(app)
    entered, release = threading.Event(), threading.Event()

    def provider(*args, **kwargs):
        entered.set()
        assert release.wait(4)
        return Summary("Must remain private", "fixture")

    monkeypatch.setattr(fleet, "summarize", provider)

    async def scenario():
        try:
            payload, _ = await ask(app, device)
            assert await asyncio.to_thread(entered.wait, 2)
            Grants(app.root).revoke(device["id"])
            await asyncio.sleep(0.15)
            assert raw(app, device, payload)["answer"] is None
            await asyncio.wait_for(app.service.close(), 0.5)
            assert raw(app, device, payload)["status"] == "cancelled"
        finally:
            release.set()

    run(app, scenario)


def test_two_real_provider_slots_survive_timeouts_until_threads_return(app, monkeypatch):
    session(app)
    devices = [phone(app) for _ in range(3)]
    release = threading.Event()
    calls = []
    lock = threading.Lock()

    def provider(*args, **kwargs):
        with lock:
            calls.append(1)
        assert release.wait(5)
        return Summary("Bounded answer", "fixture")

    monkeypatch.setattr(fleet, "summarize", provider)

    async def scenario():
        try:
            payloads = [(await ask(app, device))[0] for device in devices]
            async with asyncio.timeout(2):
                while len(calls) != 2:
                    await asyncio.sleep(0.01)
            for key in list(app.service.deadlines):
                if key[1] in {devices[0]["id"], devices[1]["id"]}:
                    app.service.deadlines[key] = 0
            async with asyncio.timeout(2):
                while raw(app, devices[0], payloads[0])["status"] != "expired":
                    await asyncio.sleep(0.01)
            assert len(calls) == 2
            assert raw(app, devices[0], payloads[0])["status"] == "expired"
            assert raw(app, devices[2], payloads[2])["status"] == "queued"
        finally:
            release.set()
        await settled(app.service)
        assert len(calls) == 3
        assert raw(app, devices[2], payloads[2])["status"] == "complete"

    run(app, scenario)


@pytest.mark.parametrize("failure", ["empty", "exception", "oversized"])
def test_provider_failure_is_sanitized_and_answers_have_byte_bound(app, monkeypatch, failure):
    session(app)
    device = phone(app)

    def provider(*args, **kwargs):
        if failure == "exception":
            raise RuntimeError("PRIVATE_PROVIDER_SECRET")
        return Summary("é" * 20000 if failure == "oversized" else "", "fixture")

    monkeypatch.setattr(fleet, "summarize", provider)

    async def scenario():
        payload, _ = await ask(app, device)
        await settled(app.service)
        value = (await app.service.lookup(device, payload["request_id"]))["receipt"]
        assert "PRIVATE_PROVIDER_SECRET" not in json.dumps(value)
        if failure == "oversized":
            assert value["status"] == "complete" and value["truncated"]
            assert len(value["answer"].encode()) == 32768
        else:
            assert value["status"] == "failed" and value["answer"] is None
            assert value["reason"] == (
                "provider_failed" if failure == "exception" else "provider_unavailable"
            )

    run(app, scenario)


def test_storage_failure_before_admission_never_schedules_model(app, monkeypatch):
    session(app)
    device = phone(app)

    async def scenario():
        context = await app.service.context(device, {"scope": {"kind": "oracle"}})

        def broken(*args):
            raise sqlite3.OperationalError("PRIVATE_DISK_DETAIL")

        monkeypatch.setattr(app.service.store, "insert", broken)
        with fails("storage_unavailable", 503):
            await app.service.accept(
                device,
                {
                    "request_id": str(uuid.uuid4()),
                    "context_id": context["context_id"],
                    "question": "Question",
                },
            )
        assert not app.service.tasks
        with fails("storage_unavailable", 503):
            await app.service.context(device, {"scope": {"kind": "oracle"}})

    run(app, scenario)


def test_restart_interrupts_pending_without_inference(app, monkeypatch):
    device = phone(app)

    async def scenario():
        monkeypatch.setattr(app.service, "_kick", lambda: None)
        payload, _ = await ask(app, device)
        replacement = Analysis(app.server, app.root)
        try:
            await replacement.start()
            result = await replacement.lookup(device, payload["request_id"])
            assert result["receipt"]["status"] == "interrupted"
            assert result["receipt"]["reason"] == "server_restarted"
            assert not replacement.tasks
        finally:
            await replacement.close()

    run(app, scenario)


def test_shutdown_replacement_cannot_exceed_two_still_running_provider_calls(app, monkeypatch):
    session(app)
    devices = [phone(app) for _ in range(3)]
    entered = []
    release = threading.Event()

    def provider(*args, **kwargs):
        entered.append(1)
        assert release.wait(5)
        return Summary("Safe answer", "fixture")

    monkeypatch.setattr(fleet, "summarize", provider)

    async def scenario():
        original = app.service
        try:
            await ask(app, devices[0])
            await ask(app, devices[1])
            async with asyncio.timeout(2):
                while len(entered) < 2:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(original.close(), 0.5)
            app.service = Analysis(app.server, app.root)
            await app.service.start()
            payload, _ = await ask(app, devices[2])
            await asyncio.sleep(0.15)
            assert len(entered) == 2
        finally:
            release.set()
        await settled(app.service)
        assert len(entered) == 3
        assert raw(app, devices[2], payload)["status"] == "complete"

    run(app, scenario)


def test_disable_then_reenable_does_not_revive_saved_answer(app):
    device = phone(app)

    async def scenario():
        payload, _ = await ask(app, device, {"kind": "folder", "path": "Empty"})
        await settled(app.service)
        await app.service.disable()
        await app.service.start()
        with fails("configuration_changed", 409):
            await app.service.lookup(device, payload["request_id"])
        context = await app.service.context(device, {"scope": {"kind": "folder", "path": "Empty"}})
        assert context["history"] == []

    run(app, scenario)


def test_storage_failure_after_inference_cannot_publish_or_be_replayed(app, monkeypatch):
    session(app)
    device = phone(app)
    entered, release = threading.Event(), threading.Event()

    def provider(*args, **kwargs):
        entered.set()
        assert release.wait(4)
        return Summary("Do not publish", "fixture")

    monkeypatch.setattr(fleet, "summarize", provider)

    async def scenario():
        try:
            payload, _ = await ask(app, device)
            assert await asyncio.to_thread(entered.wait, 2)
            transition = app.service.store.transition

            def unavailable(*args, **kwargs):
                if args[1] == "complete":
                    raise sqlite3.OperationalError("PRIVATE_STORAGE_DETAIL")
                return transition(*args, **kwargs)

            monkeypatch.setattr(app.service.store, "transition", unavailable)
        finally:
            release.set()
        await settled(app.service)
        assert raw(app, device, payload)["answer"] is None
        with fails("storage_unavailable", 503):
            await app.service.lookup(device, payload["request_id"])
        with fails("storage_unavailable", 503):
            await app.service.accept(device, payload)

    run(app, scenario)


def test_global_context_cap_and_original_whitespace_question_are_preserved(app):
    device = phone(app)

    async def scenario():
        value = await app.service.context(device, {"scope": {"kind": "oracle"}})
        original = app.service.contexts[value["context_id"]]
        for index in range(127):
            app.service.contexts[f"fixture-{index}"] = module.Context(
                {**original.row, "device": f"other-{index}"}, original.expires_at, original.deadline
            )
        with fails("context_limit", 429):
            await app.service.context(device, {"scope": {"kind": "oracle"}})
        payload = {
            "request_id": str(uuid.uuid4()),
            "context_id": value["context_id"],
            "question": "  Question?\n",
        }
        result = await app.service.accept(device, payload)
        assert result["receipt"]["question"] == payload["question"]
        await settled(app.service)

    run(app, scenario)


def test_idle_journal_is_pruned_without_another_phone_request(app, monkeypatch):
    monkeypatch.setattr(module, "SWEEP_SECONDS", 0.01)
    device = phone(app)

    async def scenario():
        payload, _ = await ask(app, device, {"kind": "folder", "path": "Empty"})
        await settled(app.service)
        now = module._now()
        monkeypatch.setattr(module, "_now", lambda: now + 24 * 60 * 60 * 1000)
        async with asyncio.timeout(1):
            while True:
                # Direct inspection deliberately does not invoke the prune helper.
                with app.service.store.connect() as db:
                    count = db.execute("SELECT count(*) FROM questions").fetchone()[0]
                if count == 0:
                    break
                await asyncio.sleep(0.01)
        assert app.service.sweeper is not None
        await app.service.close()
        assert app.service.sweeper.done()

    run(app, scenario)
