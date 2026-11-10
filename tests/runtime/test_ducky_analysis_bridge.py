"""Phone question admission uses the dedicated TLS boundary and scoped grants."""

import asyncio
import json
import secrets
import ssl
import uuid
from pathlib import Path

import pytest

from duckterm.editions import CODE, FULL, LITE
from duckterm.mobile.bridge import Bridge, start
from duckterm.mobile.cli import configure
from duckterm.mobile.grants import Grants
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setenv("DUCKTERM_HOME", str(root))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    history = HistoryStore(root / "state.sqlite", edition=FULL)
    server = Server(history=history, edition=FULL)
    history.create_folder("Empty/Nested")
    configure(root, "127.0.0.1", 4430)
    config = json.loads((root / "ducky-bridge.json").read_text())
    config["port"] = 0
    (root / "ducky-bridge.json").write_text(json.dumps(config))
    yield server, root
    server.digests.close()
    history.purge_test_sessions()
    history.close()


def phone(root, capabilities):
    grants = Grants(root)
    token = secrets.token_urlsafe(32)
    pending = grants.claim(grants.invite()["invite"], token, "Analysis fixture phone")
    grants.approve(pending["id"], capabilities=capabilities)
    return {"authorization": "Ducky " + token}, pending["id"]


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
@pytest.mark.parametrize(
    "capabilities",
    [[], ["conversation:read", "session:send"], ["analysis:folder"], ["analysis:oracle"]],
)
def test_snapshot_intersects_independent_permissions_and_edition(app, edition, capabilities):
    server, root = app
    server.edition = edition
    headers, _ = phone(root, capabilities)

    async def run():
        bridge = Bridge(server, root)
        try:
            code, result = await bridge.dispatch("GET", "/v2/snapshot", headers, b"")
            assert code == 200
            assert "analysis_jobs" in result["features"]
            assert result["authorization_revision"] == 1
            assert result["analysis"] == {
                "folder_questions": edition.includes("folder_chat")
                and "analysis:folder" in capabilities,
                "oracle_questions": edition.includes("oracle")
                and "analysis:oracle" in capabilities,
            }
            code, old = await bridge.dispatch("GET", "/v1/snapshot", headers, b"")
            assert code == 200 and old["version"] == 1 and old["read_only"] is True
            assert "analysis" not in old and "features" not in old
        finally:
            await bridge.close()

    asyncio.run(run())


@pytest.mark.parametrize("scope", [{"kind": "folder", "path": "Empty"}, {"kind": "oracle"}])
def test_conversation_permission_never_implies_analysis(app, scope):
    server, root = app
    headers, _ = phone(root, ["conversation:read", "session:send"])

    async def run():
        bridge = Bridge(server, root)
        try:
            code, result = await bridge.dispatch(
                "POST", "/v2/analysis/context", headers, json.dumps({"scope": scope}).encode()
            )
            assert code == 403 and "context_id" not in result
        finally:
            await bridge.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("GET", "/v2/analysis/context"),
        ("POST", "/v2/analysis/questions?target=/fleet/ask"),
        ("POST", "/v2/analysis/context?scope=oracle"),
        ("DELETE", "/v2/analysis/questions"),
        ("POST", "/fleet/ask"),
        ("POST", "/oracle/chat"),
        ("POST", "/v2/analysis/../messages"),
    ],
)
def test_no_generic_proxy_or_alternate_analysis_route(app, method, target):
    server, root = app
    headers, _ = phone(root, ["analysis:folder", "analysis:oracle"])

    async def run():
        bridge = Bridge(server, root)
        try:
            code, _ = await bridge.dispatch(method, target, headers, b"{}")
            assert code == 404
        finally:
            await bridge.close()

    asyncio.run(run())


@pytest.mark.parametrize("origin", ["origin", "referer"])
def test_browser_origins_are_denied_even_with_phone_credential(app, origin):
    server, root = app
    headers, _ = phone(root, ["analysis:folder"])
    headers[origin] = "https://example.com"

    async def run():
        bridge = Bridge(server, root)
        try:
            code, _ = await bridge.dispatch("POST", "/v2/analysis/context", headers, b"{}")
            assert code == 403
        finally:
            await bridge.close()

    asyncio.run(run())


def test_owner_token_is_not_phone_authority(app):
    server, root = app

    async def run():
        bridge = Bridge(server, root)
        try:
            for headers in (
                {"x-duckterm-token": server.token},
                {"authorization": "Bearer " + server.token},
            ):
                code, _ = await bridge.dispatch("POST", "/v2/analysis/context", headers, b"{}")
                assert code == 403
        finally:
            await bridge.close()

    asyncio.run(run())


def test_real_tls_context_receipt_history_and_revocation(app, monkeypatch):
    from duckterm.core import fleet_questions

    server, root = app
    headers, device_id = phone(root, ["analysis:folder"])

    def no_provider(*args, **kwargs):
        pytest.fail("Empty folder analysis must not call a model")

    monkeypatch.setattr(fleet_questions, "summarize", no_provider)

    async def run():
        bridge = await start(server, directory=root)
        assert bridge is not None and bridge.listener is not None
        port = bridge.listener.sockets[0].getsockname()[1]
        tls = ssl.create_default_context(cafile=str(root / "ducky-cert.pem"))

        async def request(method, target, body=None, *, declared=None):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, ssl=tls, server_hostname="127.0.0.1"
            )
            encoded = json.dumps(body).encode() if body is not None else b""
            length = len(encoded) if declared is None else declared
            try:
                writer.write(
                    (
                        f"{method} {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        f"Authorization: {headers['authorization']}\r\n"
                        f"Content-Length: {length}\r\n\r\n"
                    ).encode()
                    + encoded
                )
                await writer.drain()
                raw = await asyncio.wait_for(reader.read(), 4)
            finally:
                writer.close()
                await writer.wait_closed()
            header, payload = raw.split(b"\r\n\r\n", 1)
            assert b"Cache-Control: no-store" in header
            return int(header.split()[1]), json.loads(payload)

        try:
            scope = {"kind": "folder", "path": "Empty/Nested"}
            code, context = await request("POST", "/v2/analysis/context", {"scope": scope})
            assert code == 200 and context["history"] == [] and context["pending"] is None
            question = {
                "request_id": str(uuid.uuid4()),
                "context_id": context["context_id"],
                "question": "What needs attention?",
            }
            code, submitted = await request("POST", "/v2/analysis/questions", question)
            assert code == 202
            assert submitted["receipt"]["request_id"] == question["request_id"]
            target = "/v2/analysis/questions?request_id=" + question["request_id"]
            async with asyncio.timeout(3):
                while True:
                    code, response = await request("GET", target)
                    assert code == 200
                    if response["receipt"]["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(0.01)
            receipt = response["receipt"]
            assert receipt["status"] == "complete" and receipt["source_count"] == 0
            assert receipt["scope"] == scope and receipt["answer"]
            code, replay = await request("POST", "/v2/analysis/questions", question)
            assert code == 202 and replay["receipt"] == receipt
            code, updated = await request("POST", "/v2/analysis/context", {"scope": scope})
            assert code == 200 and updated["history"] == [receipt]
            assert server.history.folder_chats.snapshot("Empty/Nested")[1] == []
            assert not (root / "oracle-chat.json").exists()
            code, _ = await request("POST", "/v2/analysis/questions", declared=32769)
            assert code == 413
            code, _ = await request("POST", "/v2/analysis/context", declared=32769)
            assert code == 413
            code, _ = await request("GET", target + "&request_id=" + question["request_id"])
            assert code == 400
            Grants(root).revoke(device_id)
            code, rejected = await request("GET", target)
            assert code == 403 and "answer" not in json.dumps(rejected)
        finally:
            await bridge.close()

    asyncio.run(run())
