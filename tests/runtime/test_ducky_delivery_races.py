"""Phone delivery must revalidate after paste and when claiming an old queue row."""

import asyncio
import time

import pytest
from tests.runtime import test_ducky_messages as fixtures

from duckterm.mobile import delivery
from duckterm.mobile.message_store import TTL_MS


@pytest.fixture
def app(tmp_path, monkeypatch):
    yield from fixtures.app.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("change", ["approval", "desktop_input"])
def test_prompt_change_after_paste_never_receives_mobile_enter(app, monkeypatch, change):
    server, root = app
    service, device, _, sup, writes = fixtures.prepare(server, root, monkeypatch)
    screen = "empty prompt"
    sup.runtime.approval_prompt_visible = lambda value: value == "approval prompt"
    monkeypatch.setattr(delivery, "screen", lambda _: screen)

    def write(target, data):
        nonlocal screen
        writes.append(data)
        if data.startswith(b"\x1b[200~"):
            if change == "approval":
                screen = "approval prompt"
            else:
                # write_input (desktop approvals/legacy input API) stamps this
                # before its direct terminal write, outside the queued WS path.
                sup.last_owner_input_ms = int(time.time() * 1000)

    monkeypatch.setattr(delivery, "write", write)

    async def run():
        try:
            payload = fixtures.request(service, device)
            await service.accept(device, payload)
            queued = service.store.find(device["id"], payload["request_id"])
            await service.attempt(queued)
            assert writes == [b"\x1b[200~" + payload["text"].encode() + b"\x1b[201~"]
            result = await service.lookup(device, payload["request_id"])
            assert result["status"] == "unknown"
            assert "text" not in result
            # Reconciliation cannot authorize a second paste after partial input.
            assert (await service.accept(device, payload))["status"] == "unknown"
            await service.attempt(queued)
            assert len(writes) == 1
        finally:
            await fixtures.cleanup(service, sup)

    asyncio.run(run())


def test_row_expiring_after_ready_before_durable_claim_is_not_sent(app, monkeypatch):
    server, root = app
    service, device, _, sup, writes = fixtures.prepare(server, root, monkeypatch)

    async def run():
        try:
            payload = fixtures.request(service, device)
            await service.accept(device, payload)
            # This is the same snapshot pending() hands a worker. A later prune
            # cannot protect this already dequeued row while delivery waits.
            queued = service.store.pending()[0]
            expired_clock = (queued["created_at"] + TTL_MS + 1) / 1000

            def screen(_):
                # Simulate elapsed queue/inspection time after the event-loop
                # readiness decision but before the SQLite delivering claim.
                monkeypatch.setattr(time, "time", lambda: expired_clock)
                return "empty prompt"

            monkeypatch.setattr(delivery, "screen", screen)
            await service.attempt(queued)
            result = await service.lookup(device, payload["request_id"])
            assert result["status"] == "expired"
            assert result["reason"] == "queue_expired"
            assert "text" not in result
            assert writes == []
        finally:
            await fixtures.cleanup(service, sup)

    asyncio.run(run())
