"""Owner-only queue observations never become terminal writes or conversation history."""

import asyncio
import contextlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.runtime import test_ducky_conversation as conversation_fixtures
from tests.runtime import test_ducky_messages as fixtures
from tests.runtime.test_ducky import visible_session_fixture
from tests.runtime.test_session_api import Writer

from duckterm.editions import CODE, FULL, LITE, EditionConfig, request_features
from duckterm.mobile import delivery
from duckterm.mobile.bridge import Bridge
from duckterm.mobile.message_store import TTL_MS, MessageStore
from duckterm.mobile.messages import Messages


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    yield from fixtures.app.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def queued(app, monkeypatch):
    server, root = app
    service, device, token, sup, writes = fixtures.prepare(server, root, monkeypatch)
    server._mobile_messages = service
    yield server, root, service, device, token, sup, writes
    asyncio.run(fixtures.cleanup(service, sup))
    assert writes == []
    assert server.history._conn.execute("SELECT MIN(test) FROM sessions").fetchone()[0] in (1, None)


async def read(server, key="a", headers=None):
    writer = Writer()
    await server._dispatch(
        "GET",
        f"/sessions/{key}/mobile-messages",
        asyncio.StreamReader(),
        writer,
        headers if headers is not None else {"x-duckterm-token": server.token},
        b"",
    )
    head, body = writer.data.split(b"\r\n\r\n", 1)
    return int(head.split()[1]), json.loads(body)


def no_terminal_probes(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("A status read must never inspect or write a terminal")

    for name in ("identity", "screen", "write"):
        monkeypatch.setattr(delivery, name, unexpected)


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_real_owner_http_projection_is_bounded_and_private(queued, monkeypatch, edition):
    server, _, service, device, token, _, _ = queued
    server.edition = edition

    async def run():
        payloads = [fixtures.request(service, device, f"Fixture request {i}") for i in range(5)]
        for payload in payloads:
            await service.accept(device, payload)
        no_terminal_probes(monkeypatch)
        before = service.store.path.read_bytes()
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]

        async def get(key, credentials):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                writer.write(
                    (
                        f"GET /sessions/{key}/mobile-messages HTTP/1.1\r\n"
                        f"Host: 127.0.0.1:{port}\r\n{credentials}Connection: close\r\n\r\n"
                    ).encode()
                )
                await writer.drain()
                raw = await asyncio.wait_for(reader.read(), 3)
                head, body = raw.split(b"\r\n\r\n", 1)
                return int(head.split()[1]), json.loads(body)
            finally:
                writer.close()
                await writer.wait_closed()

        try:
            owner = f"X-Duckterm-Token: {server.token}\r\n"
            code, body = await get("a", owner)
            assert code == 200
            rows = body["mobile_messages"]
            assert len(rows) == 5
            assert [row["text"] for row in rows] == [p["text"] for p in payloads]
            assert set(rows[0]) == {
                "request_id",
                "session_id",
                "status",
                "text",
                "created_at",
                "updated_at",
                "expires_at",
                "wait_reason",
            }
            assert rows[0]["expires_at"] == rows[0]["created_at"] + TTL_MS
            assert all(
                type(rows[0][field]) is int for field in ("created_at", "updated_at", "expires_at")
            )
            assert device["id"] not in json.dumps(body) and token not in json.dumps(body)
            assert await get("other", owner) == (200, {"mobile_messages": []})
            for credentials, expected in (
                ("", 401),
                (f"Authorization: Ducky {token}\r\n", 401),
                ("Authorization: Bearer fixture-session-token\r\n", 403),
                (owner + "Authorization: Bearer fixture-session-token\r\n", 403),
                (f"X-Duckterm-Token: {token}\r\n", 401),
            ):
                status, rejected = await get("a", credentials)
                assert status == expected and "mobile_messages" not in rejected
            assert service.store.path.read_bytes() == before
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(run())


def test_unused_and_unrecovered_reads_never_create_or_recover_journal(queued):
    server, root, service, device, _, _, _ = queued

    async def run():
        assert await read(server) == (200, {"mobile_messages": []})
        assert not service.store.root.exists()
        assert MessageStore(root).pending_for_session("a", int(time.time() * 1000)) == []
        assert not service.store.root.exists()
        await service.accept(device, fixtures.request(service, device))
        before = service.store.path.read_bytes()
        server._mobile_messages = Messages(server, root)
        assert await read(server) == (200, {"mobile_messages": []})
        assert service.store.path.read_bytes() == before
        server._mobile_messages = None
        assert await read(server) == (200, {"mobile_messages": []})

    asyncio.run(run())


@pytest.mark.parametrize(
    "condition",
    [
        "deleted",
        "test",
        "archived",
        "merged",
        "stopped",
        "revoked",
        "permission",
        "host",
        "binding",
        "config",
        "disabled",
        "expired",
        "stale_delivering",
    ],
)
def test_invalid_pending_text_is_never_published(queued, monkeypatch, condition):
    server, root, service, device, _, sup, _ = queued

    async def run():
        payload = fixtures.request(service, device)
        await service.accept(device, payload)
        no_terminal_probes(monkeypatch)
        if condition == "deleted":
            server.history.delete_session("a")
        elif condition == "test":
            original = server.history.session
            monkeypatch.setattr(
                server.history, "session", lambda key: {**original(key), "test": True}
            )
        elif condition in {"archived", "merged", "stopped"}:
            server.history.set_state("a", condition)
        elif condition == "revoked":
            service.grants.revoke(device["id"])
        elif condition == "permission":
            with service.grants.locked(write=True) as state:
                state["devices"][device["id"]]["capabilities"] = ["conversation:read"]
        elif condition == "host":
            service.grants.reset()
        elif condition == "binding":
            sup._tmux_target = "rd_fixture_replacement"
        elif condition == "config":
            (root / "ducky-bridge.json").write_text('{"version":1,"changed":true}')
        elif condition == "disabled":
            (root / "ducky-bridge.json").unlink()
        else:
            with service.store.connect() as db:
                if condition == "expired":
                    db.execute(
                        "UPDATE messages SET created_at=?", (int(time.time() * 1000) - TTL_MS,)
                    )
                else:
                    db.execute(
                        "UPDATE messages SET status='delivering',updated_at=?",
                        (int(time.time() * 1000) - 60001,),
                    )
        assert await read(server) == (200, {"mobile_messages": []})

    asyncio.run(run())


@pytest.mark.parametrize("change", ["binding", "revoked", "expired"])
def test_authority_and_target_rechecked_after_async_journal_read(queued, monkeypatch, change):
    server, _, service, device, _, sup, _ = queued

    async def run():
        await service.accept(device, fixtures.request(service, device))
        original = service.store.pending_for_session
        started, resume = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def blocked(key, now):
            rows = original(key, now)
            if change == "expired":
                rows[0]["created_at"] = now - TTL_MS
            loop.call_soon_threadsafe(started.set)
            assert resume.wait(3)
            return rows

        monkeypatch.setattr(service.store, "pending_for_session", blocked)
        task = asyncio.create_task(read(server))
        try:
            await asyncio.wait_for(started.wait(), 3)
            if change == "binding":
                sup._primary_fd = 999
            elif change == "revoked":
                service.grants.revoke(device["id"])
        finally:
            resume.set()
        assert await task == (200, {"mobile_messages": []})

    asyncio.run(run())


@pytest.mark.parametrize("status", ["delivered", "expired", "cancelled", "unknown"])
def test_terminal_receipts_disappear_without_injecting_history(queued, status):
    server, _, service, device, _, _, _ = queued

    async def run():
        payload = fixtures.request(service, device)
        first = await service.accept(device, payload)
        assert (await read(server))[1]["mobile_messages"]
        row = service.store.find(device["id"], payload["request_id"])
        service.store.transition(row, status)
        assert await read(server) == (200, {"mobile_messages": []})
        result = await service.lookup(device, payload["request_id"])
        assert result["wait_reason"] is None and "text" not in result
        assert result["expires_at"] == first["expires_at"]
        assert service.store.find(device["id"], payload["request_id"])["text"] == ""

    asyncio.run(run())


@pytest.mark.parametrize(
    "condition,expected,desktop_expected",
    [
        ("idle", "safe_prompt", "safe_prompt"),
        ("waiting", "safe_prompt", "safe_prompt"),
        ("busy", "agent_busy", "agent_busy"),
        ("approval", "approval_pending", "approval_pending"),
        ("typing", "safe_prompt", "desktop_input"),
        ("delivering", "delivering", "delivering"),
    ],
)
def test_known_wait_reasons_enrich_all_phone_receipt_paths(
    queued, monkeypatch, condition, expected, desktop_expected
):
    server, root, service, device, token, sup, _ = queued
    bridge = Bridge(server, root, service)

    async def run():
        if condition in {"idle", "waiting", "busy"}:
            server.history.set_state("a", condition)
        elif condition == "approval":
            monkeypatch.setattr(
                server.approvals, "pending", lambda: [SimpleNamespace(session_key="a")]
            )
        elif condition == "typing":
            sup.last_owner_input_ms = int(time.time() * 1000)
        payload = fixtures.request(service, device)
        code, accepted = await bridge.dispatch(
            "POST",
            "/v2/messages",
            {"authorization": "Ducky " + token},
            json.dumps(payload).encode(),
        )
        assert code == 202
        if condition == "delivering":
            row = service.store.find(device["id"], payload["request_id"])
            service.store.transition(row, "delivering")
        else:
            assert accepted["receipt"]["wait_reason"] == expected
        no_terminal_probes(monkeypatch)
        code, found = await bridge.dispatch(
            "GET",
            "/v2/messages?request_id=" + payload["request_id"],
            {"authorization": "Ducky " + token},
            b"",
        )
        assert code == 200 and found["receipt"]["wait_reason"] == expected
        result = (await service.receipts(device, "a"))[0]
        assert result == found["receipt"]
        assert result["expires_at"] == accepted["receipt"]["created_at"] + TTL_MS
        code, replayed = await bridge.dispatch(
            "POST",
            "/v2/messages",
            {"authorization": "Ducky " + token},
            json.dumps(payload).encode(),
        )
        assert code == 202 and replayed["receipt"]["wait_reason"] == expected
        assert (await read(server))[1]["mobile_messages"][0]["wait_reason"] == desktop_expected

    asyncio.run(run())


def test_advisory_failure_cannot_reject_durable_acceptance(queued, monkeypatch):
    _, _, service, device, _, _, _ = queued

    def unavailable(*args):
        raise RuntimeError("fixture observation unavailable")

    async def run():
        payload = fixtures.request(service, device)
        monkeypatch.setattr(service, "wait_reason", unavailable)
        accepted = await service.accept(device, payload)
        assert accepted["status"] == "queued" and accepted["wait_reason"] == "safe_prompt"
        assert await service.accept(device, payload) == accepted
        assert (await service.lookup(device, payload["request_id"])) == accepted

    asyncio.run(run())


def test_messages_capability_is_explicit_and_required(queued):
    server, _, _, _, _, _, _ = queued
    for key in ("a", "nested/session"):
        path = f"/sessions/{key}/mobile-messages"
        assert request_features("GET", path) == ("messages",)
    server.edition = EditionConfig("lite", LITE.features - {"messages"})
    code, result = asyncio.run(read(server))
    assert code == 403 and result["feature"] == "messages"


def test_config_change_while_waiting_for_grant_lock_is_rechecked(queued, monkeypatch):
    server, root, service, device, _, _, _ = queued
    authorize = service.grants.authorized_device

    @contextlib.contextmanager
    def changed(*args, **kwargs):
        with authorize(*args, **kwargs) as live:
            (root / "ducky-bridge.json").write_text('{"version":1,"replacement":true}')
            yield live

    async def run():
        await service.accept(device, fixtures.request(service, device))
        monkeypatch.setattr(service.grants, "authorized_device", changed)
        assert await read(server) == (200, {"mobile_messages": []})

    asyncio.run(run())


@pytest.mark.parametrize("error", [OSError, ValueError, TimeoutError])
def test_unavailable_status_is_not_reported_as_an_empty_queue(queued, monkeypatch, error):
    server, _, service, device, _, _, _ = queued

    def fail(*args):
        raise error("Private fixture diagnostic must not reach the response")

    async def run():
        await service.accept(device, fixtures.request(service, device))
        monkeypatch.setattr(service.store, "pending_for_session", fail)
        assert await read(server) == (503, {"error": "mobile_status_unavailable"})

    asyncio.run(run())


@pytest.mark.parametrize("typing", [False, True])
def test_conversation_wire_receipt_is_additive_without_changing_transcript(queued, typing):
    server, root, service, device, token, sup, _ = queued
    source = conversation_fixtures.source((server, root), runtime="codex", state="idle")
    conversation_fixtures.write(source, [conversation_fixtures.codex("assistant", "Fixture reply")])
    original = source.read_bytes()
    bridge = Bridge(server, root, service)

    async def run():
        if typing:
            sup.last_owner_input_ms = int(time.time() * 1000)
        payload = fixtures.request(service, device)
        accepted = await service.accept(device, payload)
        assert accepted["wait_reason"] == "safe_prompt"
        code, result = await bridge.dispatch(
            "GET", "/v2/conversation?session_id=a", {"authorization": "Ducky " + token}, b""
        )
        assert code == 200
        assert [message["text"] for message in result["messages"]] == ["Fixture reply"]
        assert result["receipts"] == [accepted]
        legacy = {
            key: value
            for key, value in accepted.items()
            if key not in {"expires_at", "wait_reason"}
        }
        assert legacy == {
            "request_id": payload["request_id"],
            "session_id": "a",
            "status": "queued",
            "text": payload["text"],
            "reason": None,
            "created_at": accepted["created_at"],
            "updated_at": accepted["created_at"],
        }
        assert source.read_bytes() == original

    asyncio.run(run())


def test_busy_grant_lock_returns_503_without_sleep_or_queue_mutation(queued, monkeypatch):
    server, _, service, device, _, _, _ = queued
    locked, release = threading.Event(), threading.Event()
    lock_errors = []

    def hold_grant_lock():
        try:
            with service.grants.locked():
                locked.set()
                if not release.wait(5):
                    raise AssertionError("Fixture grant lock was not released")
        except BaseException as error:
            lock_errors.append(error)

    async def run():
        payloads = [fixtures.request(service, device, f"Queued fixture {i}") for i in range(5)]
        for payload in payloads:
            await service.accept(device, payload)
        no_terminal_probes(monkeypatch)
        before = service.store.path.read_bytes()
        grants_before = service.grants.path.read_bytes()
        loop_thread = threading.get_ident()
        sleep = time.sleep
        sleeps = []

        def reject_loop_sleep(seconds):
            if threading.get_ident() == loop_thread:
                sleeps.append(seconds)
                pytest.fail("A pending-message read must not sleep on the server event loop")
            sleep(seconds)

        holder = threading.Thread(target=hold_grant_lock)
        holder.start()
        try:
            assert await asyncio.to_thread(locked.wait, 3)
            with monkeypatch.context() as scoped:
                scoped.setattr(time, "sleep", reject_loop_sleep)
                assert await read(server) == (503, {"error": "mobile_status_unavailable"})
            assert sleeps == []
            assert service.store.path.read_bytes() == before
            assert service.grants.path.read_bytes() == grants_before
        finally:
            release.set()
            await asyncio.to_thread(holder.join, 3)
        assert not holder.is_alive() and not lock_errors
        code, result = await read(server)
        assert code == 200
        assert [row["request_id"] for row in result["mobile_messages"]] == [
            payload["request_id"] for payload in payloads
        ]
        service.grants.revoke(device["id"])
        assert await read(server) == (200, {"mobile_messages": []})
        assert service.store.path.read_bytes() == before

    asyncio.run(run())


def test_two_visible_sessions_have_disjoint_pending_projections(queued, monkeypatch):
    server, _, service, device, _, sup, _ = queued
    server.history.record(
        {
            "_id": "second-fixture",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "b",
            "runtime": "codex",
            "test": True,
        }
    )
    visible_session_fixture(server, monkeypatch, {"a", "b"})
    server.orchestrator._supervisors["b"] = sup

    async def run():
        payload = fixtures.request(service, device)
        await service.accept(device, payload)
        other = {
            **fixtures.request(service, device, "Only session B"),
            "session_id": "b",
            "send_context": service.fields("b", device)["send_context"],
        }
        await service.accept(device, other)
        no_terminal_probes(monkeypatch)
        for key, text in (("a", payload["text"]), ("b", other["text"])):
            status, result = await read(server, key)
            assert status == 200
            assert [row["session_id"] for row in result["mobile_messages"]] == [key]
            assert [row["text"] for row in result["mobile_messages"]] == [text]

    asyncio.run(run())
