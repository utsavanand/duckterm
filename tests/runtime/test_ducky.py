"""Real grants, TLS, and SQLite; the phone never receives desktop control authority."""

import asyncio
import base64
import hashlib
import json
import secrets
import ssl
from urllib.parse import parse_qs, urlsplit

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.editions import CODE, FULL, LITE
from duckterm.mobile.bridge import Bridge, start
from duckterm.mobile.cli import configure, invitation
from duckterm.mobile.grants import Denied, Grants
from duckterm.mobile.snapshot import snapshot
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "state.sqlite", edition=LITE)
    server = Server(history=history, edition=LITE)
    history.record(
        {
            "_id": "initial",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "a",
            "runtime": "codex",
            "cwd": "/private/project",
            "prompt": "secret prompt",
            "test": True,
        }
    )
    history.set_meta("a", group="Work", name="Coding agent", notes="private owner notes")
    yield server, tmp_path
    history.purge_test_sessions()
    history.close()


def paired(grants):
    invitation = grants.invite()
    token = secrets.token_urlsafe(32)
    pending = grants.claim(invitation["invite"], token, "Test iPhone")
    return token, pending


def test_pairing_needs_owner_approval_and_revocation_is_immediate(tmp_path):
    grants = Grants(tmp_path)
    token, pending = paired(grants)
    assert pending["status"] == "pending"
    with pytest.raises(Denied):
        grants.authenticate(token)
    assert grants.authenticate(token, pending=True)["status"] == "pending"
    grants.approve(pending["id"])
    assert Grants(tmp_path).authenticate(token)["status"] == "active"
    assert token not in grants.path.read_text()
    assert "token_hash" not in json.dumps(grants.devices())
    assert grants.path.stat().st_mode & 0o777 == 0o600
    grants.revoke(pending["id"])
    with pytest.raises(Denied):
        Grants(tmp_path).authenticate(token)


def test_invitation_claim_is_single_use_but_lost_response_can_retry(tmp_path):
    grants = Grants(tmp_path)
    invite = grants.invite(now=100)
    token = secrets.token_urlsafe(32)
    first = grants.claim(invite["invite"], token, "Phone", now=101)
    assert grants.claim(invite["invite"], token, "Phone", now=102) == first
    with pytest.raises(Denied):
        grants.claim(invite["invite"], secrets.token_urlsafe(32), "Another phone", now=102)
    with pytest.raises(Denied):
        grants.claim(invite["invite"], token, "Phone", now=701)
    with pytest.raises(Denied):
        grants.approve(first["id"], now=702)


def test_all_editions_provide_only_the_phone_projection(app):
    server, _ = app
    for edition in (LITE, CODE, FULL):
        server.edition = edition
        result = snapshot(server, "host", 100)
        assert result["read_only"] is True
        assert result["folders"] == ["Work"]
        assert result["sessions"] == [
            {
                "id": "a",
                "name": "Coding agent",
                "folder": "Work",
                "runtime": "codex",
                "state": "busy",
                "observed_at": 1,
                "approval_visibility": "terminal_only",
            }
        ]
        serialized = json.dumps(result)
        for secret in ("secret prompt", "private owner notes", "/private/project", server.token):
            assert secret not in serialized
    server.history.set_state("a", "stopped", now=2)
    assert snapshot(server, "host", 100)["sessions"] == []


def test_notifications_are_observed_turns_and_attention_with_replay_detection(app):
    server, _ = app
    initial = server.bus.publish(
        {"event_type": "UserPromptSubmit", "session_key": "a", "test": True}
    )
    server.bus.publish(
        {
            "event_type": "Stop",
            "session_key": "a",
            "hook_event": True,
            "stop_hook_active": True,
            "test": True,
        }
    )
    server.bus.publish(
        {
            "event_type": "Stop",
            "session_key": "a",
            "hook_event": True,
            "agent_id": "child",
            "test": True,
        }
    )
    end = server.bus.publish(
        {
            "event_type": "Stop",
            "session_key": "a",
            "hook_event": True,
            "stop_hook_active": False,
            "test": True,
        }
    )
    attention = server.bus.publish(
        {
            "event_type": "Notification",
            "session_key": "a",
            "notification_type": "permission_prompt",
            "test": True,
        }
    )
    result = snapshot(server, "host", 100, initial["_id"])
    assert [(n["kind"], n["id"]) for n in result["notices"]] == [
        ("turn_finished", end["_id"]),
        ("attention", attention["_id"]),
    ]
    assert snapshot(server, "host", 100, result["cursor"])["notices"] == []
    assert snapshot(server, "host", 100)["notices"] == []
    reset = snapshot(server, "host", 100, "previousprocess")
    assert reset["reset"] is True and reset["notices"] == []


def test_first_notification_after_an_empty_snapshot_is_delivered_without_replay(app):
    server, _ = app
    initial = snapshot(server, "host", 100, initial_cursor="emptyring")
    assert initial["cursor"] == "emptyring" and initial["notices"] == []
    attention = server.bus.publish(
        {
            "event_type": "Notification",
            "notification_type": "permission_prompt",
            "session_key": "a",
            "test": True,
        }
    )
    current = snapshot(server, "host", 200, initial["cursor"], initial_cursor="emptyring")
    assert [notice["id"] for notice in current["notices"]] == [attention["_id"]]
    assert not current["reset"]
    assert (
        snapshot(server, "host", 300, current["cursor"], initial_cursor="emptyring")["notices"]
        == []
    )
    restarted = snapshot(server, "host", 300, initial["cursor"], initial_cursor="newbridge")
    assert restarted["reset"] and restarted["notices"] == []
    for _ in range(500):
        server.bus.publish({"event_type": "Notification", "session_key": "a", "test": True})
    overrun = snapshot(server, "host", 400, initial["cursor"], initial_cursor="emptyring")
    assert overrun["reset"] and overrun["notices"] == []


def test_bridge_rejects_desktop_reads_mutations_and_cross_credential_use(app):
    server, root = app
    (root / "ducky-bridge.json").write_text("{}")
    bridge = Bridge(server, root)
    token, device = paired(bridge.grants)
    bridge.grants.approve(device["id"])
    headers = {"authorization": "Ducky " + token}

    async def run():
        assert (await bridge.dispatch("GET", "/v1/snapshot", headers, b""))[0] == 200
        for method, route in [
            ("GET", "/"),
            ("GET", "/events"),
            ("GET", "/sessions/a/messages"),
            ("GET", "/file?path=anything"),
            ("POST", "/sessions/a/input"),
            ("POST", "/sessions/a/stop"),
            ("POST", "/approvals/a/decide"),
            ("POST", "/file"),
            ("DELETE", "/sessions/a"),
            ("POST", "/v1/snapshot"),
        ]:
            assert (await bridge.dispatch(method, route, headers, b"{}"))[0] == 404
        assert (
            await bridge.dispatch("GET", "/v1/snapshot", {"x-duckterm-token": server.token}, b"")
        )[0] == 403
        assert (
            await bridge.dispatch(
                "GET", "/v1/snapshot", {**headers, "origin": "https://evil.example"}, b""
            )
        )[0] == 403
        bridge.grants.revoke(device["id"])
        assert (await bridge.dispatch("GET", "/v1/snapshot", headers, b""))[0] == 403

    asyncio.run(run())
    for path in ("/sessions/a/stop", "/sessions/a/input", "/file", "/approvals/a/decide"):
        assert dispatch(server, "POST", path, headers, b"{}")[0] == 401


def test_tls_pairing_snapshot_and_disable_on_real_socket(app):
    server, root = app
    configure(root, "127.0.0.1", 4430)
    payload = json.loads(
        base64.urlsafe_b64decode(parse_qs(urlsplit(invitation(root)).query)["invite"][0] + "==")
    )
    assert (
        payload["certificate_sha256"]
        == hashlib.sha256(
            ssl.PEM_cert_to_DER_cert((root / "ducky-cert.pem").read_text())
        ).hexdigest()
    )
    # Dynamic listener port avoids touching any other instance.
    config = json.loads((root / "ducky-bridge.json").read_text())
    config["port"] = 0
    (root / "ducky-bridge.json").write_text(json.dumps(config))
    token = secrets.token_urlsafe(32)

    async def run():
        bridge = await start(server)
        assert bridge is not None
        port = bridge.listener.sockets[0].getsockname()[1]
        context = ssl.create_default_context(cafile=str(root / "ducky-cert.pem"))

        async def request(method, path, body=None, auth=None):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, ssl=context, server_hostname="127.0.0.1"
            )
            data = json.dumps(body).encode() if body is not None else b""
            head = f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            head += f"Content-Length: {len(data)}\r\n"
            if auth:
                head += "Authorization: Ducky " + auth + "\r\n"
            writer.write(head.encode() + b"\r\n" + data)
            await writer.drain()
            raw = await reader.read()
            writer.close()
            await writer.wait_closed()
            headers, response = raw.split(b"\r\n\r\n", 1)
            assert b"Cache-Control: no-store" in headers
            return int(headers.split()[1]), json.loads(response)

        try:
            code, pending = await request(
                "POST",
                "/v1/pair",
                {"invite": payload["invite"], "token": token, "name": "Test phone"},
            )
            assert code == 202 and pending["status"] == "pending"
            assert (await request("GET", "/v1/snapshot", auth=token))[0] == 403
            bridge.grants.approve(pending["id"])
            code, data = await request("GET", "/v1/snapshot", auth=token)
            assert code == 200 and data["sessions"][0]["name"] == "Coding agent"
            (root / "ducky-bridge.json").unlink()
            assert (await request("GET", "/v1/snapshot", auth=token))[0] == 503
            (root / "ducky-bridge.json").write_text(json.dumps(config))
            assert (await request("GET", "/v1/snapshot", auth=token))[0] == 200
            bridge.grants.revoke(pending["id"])
            assert (await request("GET", "/v1/snapshot", auth=token))[0] == 403
        finally:
            await bridge.close()

    asyncio.run(run())


def test_unconfigured_bridge_creates_no_identity_or_listener(app):
    server, root = app
    before = set(root.iterdir())
    assert asyncio.run(start(server)) is None
    assert set(root.iterdir()) == before


def test_broken_optional_bridge_does_not_stop_the_desktop(app, monkeypatch):
    from unittest.mock import AsyncMock

    server, _ = app
    monkeypatch.setattr(server.orchestrator, "reconcile", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "duckterm.mobile.bridge.start", AsyncMock(side_effect=ValueError("bad TLS identity"))
    )
    original_start = asyncio.start_server

    async def run():
        listening = asyncio.get_running_loop().create_future()

        async def capture(*args, **kwargs):
            listener = await original_start(*args, **kwargs)
            listening.set_result(listener.sockets[0].getsockname()[1])
            return listener

        monkeypatch.setattr(asyncio, "start_server", capture)
        task = asyncio.create_task(server.serve("127.0.0.1", 0))
        try:
            port = await asyncio.wait_for(listening, 5)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"GET /sessions HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 5)
            assert response.startswith(b"HTTP/1.1 200")
            assert b"Coding agent" in response
            writer.close()
            await writer.wait_closed()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


def test_identity_replacement_recovers_address_and_revokes_old_phones(tmp_path):
    configure(tmp_path, "192.168.1.20", 4431)
    grants = Grants(tmp_path)
    old_token, device = paired(grants)
    grants.approve(device["id"])
    old_invite = grants.invite()
    old_cert = (tmp_path / "ducky-cert.pem").read_bytes()
    with pytest.raises(ValueError, match="setup --replace"):
        configure(tmp_path, "192.168.1.35", 4431)
    assert grants.authenticate(old_token)["status"] == "active"
    configure(tmp_path, "192.168.1.35", 4431, replace=True)
    assert (tmp_path / "ducky-cert.pem").read_bytes() != old_cert
    assert json.loads((tmp_path / "ducky-bridge.json").read_text())["address"] == "192.168.1.35"
    assert grants.devices() == []
    with pytest.raises(Denied):
        grants.authenticate(old_token)
    with pytest.raises(Denied):
        grants.claim(old_invite["invite"], secrets.token_urlsafe(32), "Old invite")
    new_invite = grants.invite()
    assert new_invite["host_id"] != old_invite["host_id"]
    assert (
        grants.claim(new_invite["invite"], secrets.token_urlsafe(32), "New phone")["status"]
        == "pending"
    )


def test_failed_certificate_generation_preserves_existing_access(tmp_path, monkeypatch):
    import subprocess

    from duckterm.mobile import cli

    configure(tmp_path, "127.0.0.1", 4431)
    grants = Grants(tmp_path)
    token, device = paired(grants)
    grants.approve(device["id"])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}

    def failed(*args, **kwargs):
        raise subprocess.TimeoutExpired("openssl", 30)

    monkeypatch.setattr(cli.subprocess, "run", failed)
    with pytest.raises(subprocess.TimeoutExpired):
        configure(tmp_path, "192.168.1.35", 4431, replace=True)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    assert grants.authenticate(token)["status"] == "active"
