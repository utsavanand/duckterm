"""Hot phone-listener lifecycle using scratch identities and real loopback TLS."""

import asyncio
import contextlib
import json
import secrets
import socket
import ssl
from unittest.mock import AsyncMock

import pytest

from duckterm.editions import LITE
from duckterm.helpers.private_files import private_write
from duckterm.mobile import manager as manager_module
from duckterm.mobile.cli import configure, enable
from duckterm.mobile.grants import Grants
from duckterm.mobile.manager import Manager
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "state.sqlite", edition=LITE)
    server = Server(history=history, edition=LITE)
    history.record(
        {
            "_id": "manager-fixture",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "manager-test",
            "runtime": "codex",
            "test": True,
        }
    )
    yield server, tmp_path
    history.purge_test_sessions()
    history.close()


def free_port(host="127.0.0.1"):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as reservation:
        reservation.bind((host, 0))
        return reservation.getsockname()[1]


def setup(root):
    port = free_port()
    configure(root, "127.0.0.1", port, hostname="ducky-fixture.local", interface="en0")
    return port


def status(root):
    return json.loads((root / "ducky-status.json").read_text())


async def request(root, host, port, token=None):
    context = ssl.create_default_context(cafile=str(root / "ducky-cert.pem"))
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=context, server_hostname="ducky-fixture.local"),
        3,
    )
    try:
        head = "GET /v1/snapshot HTTP/1.1\r\nHost: ducky-fixture.local\r\n"
        if token is not None:
            head += f"Authorization: Ducky {token}\r\n"
        writer.write((head + "\r\n").encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 3)
        headers, body = response.split(b"\r\n\r\n", 1)
        return int(headers.split()[1]), json.loads(body)
    finally:
        writer.close()
        await writer.wait_closed()


async def closed(host, port):
    with pytest.raises(OSError):
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 3)
        writer.close()
        await writer.wait_closed()


def test_without_opt_in_manager_never_discovers_or_creates_identity(app, monkeypatch):
    server, root = app
    discover = AsyncMock()
    monkeypatch.setattr(manager_module.network, "detect_address", discover)
    before = set(root.iterdir())
    manager = Manager(server, root)
    asyncio.run(manager.tick())
    assert manager.bridge is None and manager.signature is None
    assert set(root.iterdir()) == before
    discover.assert_not_called()


def test_setup_disable_and_enable_work_on_existing_manager(app, monkeypatch):
    server, root = app
    monkeypatch.setattr(manager_module.network, "detect_address", lambda **_: "127.0.0.1")

    async def run():
        manager = Manager(server, root)
        try:
            await manager.tick()
            port = setup(root)
            await manager.tick()
            assert status(root)["state"] == "ready"
            assert (await request(root, "127.0.0.1", port))[0] == 403
            original = manager.bridge
            await manager.tick()
            assert manager.bridge is original  # Stable network does not churn connections.
            (root / "ducky-bridge.json").unlink()
            await manager.tick()
            assert status(root)["state"] == "disabled"
            assert original.listener is not None and not original.listener.is_serving()
            await closed("127.0.0.1", port)
            enable(root)
            await manager.tick()
            assert manager.bridge is not original
            assert (await request(root, "127.0.0.1", port))[0] == 403
        finally:
            await manager.close_listener()

    asyncio.run(run())


def test_address_change_preserves_certificate_and_approved_phone(app, monkeypatch):
    server, root = app
    port = setup(root)
    current = ["127.0.0.1"]
    monkeypatch.setattr(manager_module.network, "detect_address", lambda **_: current[0])
    grants = Grants(root)
    invitation = grants.invite()
    token = secrets.token_urlsafe(32)
    phone = grants.claim(invitation["invite"], token, "Fixture phone")
    grants.approve(phone["id"])
    identity = {name: (root / name).read_bytes() for name in ("ducky-cert.pem", "ducky-key.pem")}

    async def run():
        manager = Manager(server, root)
        try:
            await manager.tick()
            first = manager.bridge
            code, before = await request(root, "127.0.0.1", port, token)
            assert code == 200
            # Both actual listeners stay on loopback. Simulate switching to the
            # IPv6 address of the selected interface without changing the host network.
            current[0] = "::1"
            config = json.loads((root / "ducky-bridge.json").read_text())
            config["family"] = 6
            private_write(root / "ducky-bridge.json", json.dumps(config))
            await manager.tick()
            assert manager.bridge is not first
            assert status(root)["address"] == "::1"
            await closed("127.0.0.1", port)
            code, after = await request(root, "::1", port, token)
            assert code == 200
            assert after["host_id"] == before["host_id"] == invitation["host_id"]
            assert after["sessions"] == before["sessions"]
            assert Grants(root).authenticate(token)["id"] == phone["id"]
            assert identity == {name: (root / name).read_bytes() for name in identity}
        finally:
            await manager.close_listener()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [ValueError("ambiguous interface"), "8.8.8.8"])
def test_invalid_discovery_closes_old_listener_and_recovers(app, monkeypatch, failure):
    server, root = app
    port = setup(root)
    current = ["127.0.0.1"]

    def discover(**_):
        if isinstance(current[0], Exception):
            raise current[0]
        return current[0]

    monkeypatch.setattr(manager_module.network, "detect_address", discover)

    async def run():
        manager = Manager(server, root)
        try:
            await manager.tick()
            current[0] = failure
            await manager.tick()
            assert manager.bridge is None and manager.signature is None
            assert status(root)["state"] == "unavailable"
            await closed("127.0.0.1", port)
            current[0] = "127.0.0.1"
            await manager.tick()
            assert (await request(root, "127.0.0.1", port))[0] == 403
        finally:
            await manager.close_listener()

    asyncio.run(run())


def test_bind_failure_retries_after_port_is_available(app, monkeypatch):
    server, root = app
    port = setup(root)
    monkeypatch.setattr(manager_module.network, "detect_address", lambda **_: "127.0.0.1")

    async def run():
        manager = Manager(server, root)
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", port))
            occupied.listen()
            await manager.tick()
            assert manager.bridge is None
            assert status(root)["state"] == "unavailable"
        try:
            await manager.tick()
            assert (await request(root, "127.0.0.1", port))[0] == 403
        finally:
            await manager.close_listener()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["disable", "port"])
def test_configuration_change_during_start_does_not_leave_old_listener(app, monkeypatch, change):
    server, root = app
    port = setup(root)
    monkeypatch.setattr(manager_module.network, "detect_address", lambda **_: "127.0.0.1")
    real_start = manager_module.start
    created = []

    async def change_after_bind(*args, **kwargs):
        bridge = await real_start(*args, **kwargs)
        created.append(bridge)
        if change == "disable":
            (root / "ducky-bridge.json").unlink()
        else:
            config = json.loads((root / "ducky-bridge.json").read_text())
            config["port"] = free_port()
            private_write(root / "ducky-bridge.json", json.dumps(config))
        return bridge

    monkeypatch.setattr(manager_module, "start", change_after_bind)

    async def run():
        manager = Manager(server, root)
        try:
            await manager.tick()
            assert len(created) == 1
            assert not created[0].listener.is_serving()
            assert manager.bridge is None and manager.signature is None
            assert manager.status is None or manager.status["state"] != "ready"
            await closed("127.0.0.1", port)
        finally:
            await manager.close_listener()

    asyncio.run(run())


def test_running_desktop_survives_discovery_error_and_hot_setup(app, monkeypatch):
    server, root = app
    monkeypatch.setattr(server.orchestrator, "reconcile", AsyncMock(return_value=[]))
    current = ["127.0.0.1"]

    def discover(**_):
        if isinstance(current[0], Exception):
            raise current[0]
        return current[0]

    monkeypatch.setattr(manager_module.network, "detect_address", discover)
    real_start = asyncio.start_server

    async def run():
        listening = asyncio.get_running_loop().create_future()

        async def capture(*args, **kwargs):
            listener = await real_start(*args, **kwargs)
            if not kwargs.get("ssl") and not listening.done():
                listening.set_result(listener.sockets[0].getsockname()[1])
            return listener

        monkeypatch.setattr(asyncio, "start_server", capture)
        desktop = asyncio.create_task(server.serve("127.0.0.1", 0))

        async def wait_state(expected):
            async with asyncio.timeout(6):
                while (
                    not (root / "ducky-status.json").exists() or status(root)["state"] != expected
                ):
                    assert not desktop.done()
                    await asyncio.sleep(0.02)

        async def desktop_ok(port):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                writer.write(
                    (
                        f"GET /sessions HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        f"X-Duckterm-Token: {server.token}\r\n\r\n"
                    ).encode()
                )
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), 3)
                assert response.startswith(b"HTTP/1.1 200")
                assert b"manager-test" in response
            finally:
                writer.close()
                await writer.wait_closed()

        try:
            desktop_port = await asyncio.wait_for(listening, 5)
            await desktop_ok(desktop_port)
            assert not (root / "ducky-status.json").exists()
            mobile_port = setup(root)
            await wait_state("ready")
            assert (await request(root, "127.0.0.1", mobile_port))[0] == 403
            current[0] = ValueError("two eligible addresses")
            await wait_state("unavailable")
            await closed("127.0.0.1", mobile_port)
            await desktop_ok(desktop_port)
            current[0] = "127.0.0.1"
            await wait_state("ready")
            assert (await request(root, "127.0.0.1", mobile_port))[0] == 403
            await desktop_ok(desktop_port)
        finally:
            desktop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await desktop
        assert status(root)["state"] == "stopped"
        await closed("127.0.0.1", mobile_port)
        await closed("127.0.0.1", desktop_port)

    asyncio.run(run())
