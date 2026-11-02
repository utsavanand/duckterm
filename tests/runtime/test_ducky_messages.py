"""Phone writes use real grants/journals and isolated, observable terminal doubles."""

import asyncio
import json
import secrets
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from tests.runtime import test_ducky as base_fixtures

from duckterm.core.orchestrator import SessionSupervisor
from duckterm.mobile import delivery
from duckterm.mobile.bridge import Bridge
from duckterm.mobile.grants import Grants
from duckterm.mobile.message_store import MessageError, MessageStore
from duckterm.mobile.messages import Messages, validate
from duckterm.runtimes.generic import GenericRuntime


@pytest.fixture
def app(tmp_path, monkeypatch):
    yield from base_fixtures.app.__wrapped__(tmp_path, monkeypatch)


def prepare(server, root, monkeypatch, capabilities=None):
    (root / "ducky-bridge.json").write_text('{"version":1}')
    grants = Grants(root)
    invitation = grants.invite()
    token = secrets.token_urlsafe(32)
    phone = grants.claim(invitation["invite"], token, "Message fixture")
    grants.approve(phone["id"], capabilities=capabilities or ["conversation:read", "session:send"])
    device = grants.authenticate(token)
    sup = SessionSupervisor(
        bus=server.bus,
        runtime=GenericRuntime("cat"),
        session_key="a",
        cwd=str(root),
        extra={"test": True},
    )
    sup.runtime = SimpleNamespace(
        name="codex", prompt_is_empty=lambda _: True, approval_prompt_visible=lambda _: False
    )
    sup._tmux_target = "rd_test_mobile"
    server.orchestrator._supervisors["a"] = sup
    server.history.record(
        {
            "_id": uuid.uuid4().hex,
            "_ts": int(time.time() * 1000),
            "event_type": "Stop",
            "session_key": "a",
            "test": True,
        }
    )
    writes = []
    monkeypatch.setattr(delivery, "identity", lambda _: "%1:123:0")
    monkeypatch.setattr(delivery, "screen", lambda _: "empty prompt")
    monkeypatch.setattr(delivery, "write", lambda target, data: writes.append(data))
    service = Messages(server, root)
    monkeypatch.setattr(service, "ensure_worker", lambda: None)
    return service, device, token, sup, writes


def request(service, device, text="Please summarize the current changes"):
    return {
        "session_id": "a",
        "send_context": service.fields("a", device)["send_context"],
        "request_id": str(uuid.uuid4()),
        "text": text,
    }


async def cleanup(service, sup):
    await service.close()
    if sup._input_task:
        sup._input_task.cancel()
        await asyncio.gather(sup._input_task, return_exceptions=True)


def test_durable_accept_retry_and_one_terminal_delivery(app, monkeypatch):
    server, root = app
    service, device, _, sup, writes = prepare(server, root, monkeypatch)

    async def run():
        try:
            payload = request(service, device)
            first = await service.accept(device, payload)
            assert first["status"] == "queued" and writes == []
            assert await service.accept(device, payload) == first
            with pytest.raises(MessageError) as conflict:
                await service.accept(device, {**payload, "text": "different"})
            assert conflict.value.status == 409
            row = service.store.find(device["id"], payload["request_id"])
            await service.attempt(row)
            assert writes == [b"\x1b[200~" + payload["text"].encode() + b"\x1b[201~", b"\r"]
            result = await service.accept(device, payload)
            assert result["status"] == "delivered" and "text" not in result
            await service.attempt(row)
            assert len(writes) == 2
            assert service.store.find(device["id"], payload["request_id"])["text"] == ""
            assert sup.last_owner_input_ms == 0  # Phone sends are not desktop keystrokes.
            assert service.store.path.stat().st_mode & 0o777 == 0o600
            assert service.store.root.stat().st_mode & 0o777 == 0o700
        finally:
            await cleanup(service, sup)

    asyncio.run(run())


@pytest.mark.parametrize("condition", ["busy", "waiting", "draft", "typing", "approval", "restart"])
def test_unsafe_prompt_remains_queued(app, monkeypatch, condition):
    server, root = app
    service, device, _, sup, writes = prepare(server, root, monkeypatch)

    async def run():
        try:
            payload = request(service, device)
            await service.accept(device, payload)
            if condition in {"busy", "waiting"}:
                server.history.record(
                    {
                        "_id": uuid.uuid4().hex,
                        "_ts": int(time.time() * 1000) + 1,
                        "event_type": "PreToolUse" if condition == "busy" else "Notification",
                        "session_key": "a",
                        "test": True,
                    }
                )
            elif condition == "draft":
                sup.runtime.prompt_is_empty = lambda _: False
            elif condition == "typing":
                sup.last_owner_input_ms = int(time.time() * 1000)
            elif condition == "approval":
                sup.runtime.approval_prompt_visible = lambda _: True
            else:
                monkeypatch.setattr(server.restarts, "pending", lambda _: True)
            row = service.store.find(device["id"], payload["request_id"])
            await service.attempt(row)
            result = await service.lookup(device, payload["request_id"])
            assert result["status"] == ("cancelled" if condition == "restart" else "queued")
            assert writes == []
        finally:
            await cleanup(service, sup)

    asyncio.run(run())


@pytest.mark.parametrize("condition", ["revoked", "replaced", "ended", "disabled"])
def test_target_or_authority_loss_never_writes(app, monkeypatch, condition):
    server, root = app
    service, device, _, sup, writes = prepare(server, root, monkeypatch)

    async def run():
        try:
            payload = request(service, device)
            await service.accept(device, payload)
            if condition == "revoked":
                service.grants.revoke(device["id"])
            elif condition == "replaced":
                sup._tmux_target = "rd_replacement"
            elif condition == "ended":
                server.history.record(
                    {
                        "_id": uuid.uuid4().hex,
                        "_ts": int(time.time() * 1000) + 1,
                        "event_type": "SessionEnd",
                        "session_key": "a",
                        "test": True,
                    }
                )
            else:
                (root / "ducky-bridge.json").unlink()
                await service.disable()
            row = service.store.find(device["id"], payload["request_id"])
            await service.attempt(row)
            assert (await service.lookup(device, payload["request_id"]))["status"] == "cancelled"
            assert writes == []
        finally:
            await cleanup(service, sup)

    asyncio.run(run())


def test_partial_write_unknown_is_never_replayed(app, monkeypatch):
    server, root = app
    service, device, _, sup, writes = prepare(server, root, monkeypatch)

    def partial(target, data):
        writes.append(data)
        raise TimeoutError()

    monkeypatch.setattr(delivery, "write", partial)

    async def run():
        try:
            payload = request(service, device)
            await service.accept(device, payload)
            row = service.store.find(device["id"], payload["request_id"])
            await service.attempt(row)
            assert (await service.accept(device, payload))["status"] == "unknown"
            await service.attempt(row)
            assert len(writes) == 1 and writes[0] != b"\r"
        finally:
            await cleanup(service, sup)

    asyncio.run(run())


def test_process_restart_cancels_queued_and_marks_started_unknown(app, monkeypatch):
    server, root = app
    service, device, _, sup, writes = prepare(server, root, monkeypatch)

    async def run():
        try:
            one, two = request(service, device), request(service, device)
            await service.accept(device, one)
            await service.accept(device, two)
            service.store.transition(
                service.store.find(device["id"], one["request_id"]), "delivering"
            )
            replacement = MessageStore(root)
            replacement.initialize()
            assert replacement.find(device["id"], one["request_id"])["status"] == "unknown"
            assert replacement.find(device["id"], two["request_id"])["status"] == "cancelled"
            assert replacement.pending() == [] and writes == []
        finally:
            await cleanup(service, sup)

    asyncio.run(run())


def test_expired_context_cannot_create_new_work_but_retry_can_reconcile(app, monkeypatch):
    server, root = app
    service, device, _, sup, writes = prepare(server, root, monkeypatch)

    async def run():
        try:
            payload = request(service, device)
            await service.accept(device, payload)
            service.secret = secrets.token_bytes(32)
            assert (await service.accept(device, payload))["status"] == "queued"
            with pytest.raises(MessageError) as stale:
                await service.accept(device, {**payload, "request_id": str(uuid.uuid4())})
            assert stale.value.reason == "stale_context" and writes == []
        finally:
            await cleanup(service, sup)

    asyncio.run(run())


def test_v2_scope_enforcement_and_receipt_device_isolation(app, monkeypatch):
    server, root = app
    service, device, token, sup, writes = prepare(server, root, monkeypatch)
    bridge = Bridge(server, root, service)

    async def call(method, path, payload=None, auth=token):
        return await bridge.dispatch(
            method,
            path,
            {"authorization": "Ducky " + auth},
            json.dumps(payload).encode() if payload else b"",
        )

    async def run():
        try:
            code, projection = await call("GET", "/v2/snapshot")
            assert code == 200 and projection["version"] == 2
            assert projection["sessions"][0]["can_send"] is True
            assert (await call("GET", "/v1/snapshot"))[1]["version"] == 1
            payload = request(service, device)
            code, response = await call("POST", "/v2/messages", payload)
            assert code == 202 and response["receipt"]["status"] == "queued"
            assert response["host_id"] == device["host_id"]
            invite = service.grants.invite()
            other = secrets.token_urlsafe(32)
            pending = service.grants.claim(invite["invite"], other, "Other test phone")
            service.grants.approve(pending["id"])
            assert (await call("POST", "/v2/messages", payload, other))[0] == 403
            assert (await call("GET", "/v2/conversation?session_id=a", auth=other))[0] == 403
            invite = service.grants.invite()
            other = secrets.token_urlsafe(32)
            pending = service.grants.claim(invite["invite"], other, "Third test phone")
            service.grants.approve(pending["id"], capabilities=["session:send"])
            assert (
                await call("GET", "/v2/messages?request_id=" + payload["request_id"], auth=other)
            )[0] == 404
            assert (await call("GET", "/v2/conversation?session_id=a", auth=other))[0] == 403
            assert writes == []
        finally:
            await cleanup(service, sup)

    asyncio.run(run())


@pytest.mark.parametrize(
    "text", ["", "  ", "\x1b[2J", "x\x00", "x\x7f", "x\u0085", "\ud800", "x\ry"]
)
def test_message_controls_and_empty_text_rejected(text):
    with pytest.raises(MessageError):
        validate(
            {"session_id": "a", "send_context": "c", "request_id": str(uuid.uuid4()), "text": text}
        )


def test_text_limit_uses_utf8_and_normalizes_crlf():
    value = {
        "session_id": "a",
        "send_context": "c",
        "request_id": str(uuid.uuid4()),
        "text": "x\r\ny\t",
    }
    assert validate(value)["text"] == "x\ny\t"
    with pytest.raises(MessageError) as size:
        validate({**value, "text": "🦆" * 1025})
    assert size.value.status == 413


def test_compound_prompt_does_not_interleave_desktop_bytes(app, monkeypatch):
    server, root = app
    service, device, _, sup, _ = prepare(server, root, monkeypatch)

    async def run():
        entered, release = threading.Event(), threading.Event()
        writes = []

        def action():
            writes.append(b"paste")
            entered.set()
            assert release.wait(2)
            writes.append(b"enter")
            return True

        monkeypatch.setattr(sup, "write_bytes", lambda data: writes.append(data) or True)
        pending = asyncio.create_task(sup.write_queued_action(action, lambda: True))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            sup.queue_bytes(b"desktop")
            release.set()
            assert await pending
            assert await sup.write_queued_bytes(b"after")
            assert b"".join(writes) == b"pasteenterdesktopafter"
        finally:
            release.set()
            await cleanup(service, sup)

    asyncio.run(run())


@pytest.mark.parametrize("method,value", [("write_bytes", b"other"), ("write_input", "other")])
def test_direct_input_cannot_split_compound_action(app, monkeypatch, method, value):
    server, root = app
    service, device, _, sup, _ = prepare(server, root, monkeypatch)

    async def run():
        entered, release = threading.Event(), threading.Event()
        writes = []

        def action():
            writes.append("paste")
            entered.set()
            assert release.wait(2)
            writes.append("enter")
            return True

        monkeypatch.setattr(sup, "_write_bytes", lambda _: writes.append("other") or True)
        monkeypatch.setattr(sup, "_write_input", lambda _: writes.append("other") or True)
        pending = asyncio.create_task(sup.write_queued_action(action, lambda: True))
        other = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            other = asyncio.create_task(asyncio.to_thread(getattr(sup, method), value))
            await asyncio.sleep(0.05)
            assert not other.done() and writes == ["paste"]
            release.set()
            assert await pending and await other
            assert writes == ["paste", "enter", "other"]
        finally:
            release.set()
            if other:
                await asyncio.gather(other, return_exceptions=True)
            await cleanup(service, sup)

    asyncio.run(run())
