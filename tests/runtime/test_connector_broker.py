"""Real TLS/stdio relay: no client cert, no tools; disable cuts an active stream."""

import asyncio
import json
import os
import shutil
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

from duckterm import connector_broker


class FakeStore:
    def __init__(self) -> None:
        self.reads: list[str] = []

    def read(self, reference: str) -> dict[str, str]:
        self.reads.append(reference)
        assert reference == "projects/test/secrets/github/versions/1"
        return {"token": "private-test-token"}


def certificates(path: Path) -> None:
    def openssl(*args: str) -> None:
        subprocess.run(["openssl", *args], cwd=path, check=True, capture_output=True)

    openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
        "-days",
        "1",
        "-subj",
        "/CN=Test CA",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    for name in ("server", "workspace"):
        openssl(
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            name + ".key",
            "-out",
            name + ".csr",
            "-subj",
            "/CN=" + name,
        )
        (path / "ext").write_text(f"subjectAltName=DNS:{name}\n")
        openssl(
            "x509",
            "-req",
            "-in",
            name + ".csr",
            "-CA",
            "ca.crt",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-out",
            name + ".crt",
            "-days",
            "1",
            "-extfile",
            "ext",
        )


@pytest.mark.skipif(not shutil.which("openssl"), reason="TLS test needs openssl")
def test_certificate_required_and_disable_closes_stdio(tmp_path: Path, monkeypatch) -> None:
    certificates(tmp_path)
    config = tmp_path / "config.json"
    settings = {
        "workspaces": {"workspace": ["github"]},
        "connectors": {
            "github": {
                "enabled": True,
                "reference": "projects/test/secrets/github/versions/1",
                "identity": "test-user",
                "write_access": False,
            }
        },
    }
    config.write_text(json.dumps(settings))
    fake = tmp_path / "fake.py"
    fake.write_text("import sys\nfor line in sys.stdin:\n print(line.rstrip(), flush=True)\n")
    monkeypatch.setattr(
        connector_broker,
        "command",
        lambda name, cred, write: ([sys.executable, str(fake)], dict(os.environ)),
    )
    store = FakeStore()
    broker = connector_broker.Broker(config, store)

    async def scenario() -> None:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(tmp_path / "server.crt", tmp_path / "server.key")
        tls.load_verify_locations(tmp_path / "ca.crt")
        tls.verify_mode = ssl.CERT_REQUIRED
        server = await asyncio.start_server(broker.handle, "127.0.0.1", 0, ssl=tls)
        port = server.sockets[0].getsockname()[1]
        client = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
        try:
            # TLS 1.3 may return from connect before the peer's rejection arrives.
            try:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", port, ssl=client, server_hostname="server"
                )
                writer.write(b'{"name":"github"}\n')
                await writer.drain()
                assert await asyncio.wait_for(reader.read(), 3) == b""
                writer.close()
            except (ssl.SSLError, ConnectionError):
                pass
            assert store.reads == []
            client.load_cert_chain(tmp_path / "workspace.crt", tmp_path / "workspace.key")
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, ssl=client, server_hostname="server"
            )
            writer.write(b'{"name":"github"}\n')
            await writer.drain()
            assert json.loads(await asyncio.wait_for(reader.readline(), 3)) == {"ready": True}
            writer.write(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n')
            await writer.drain()
            assert b'"tools/list"' in await asyncio.wait_for(reader.readline(), 3)
            settings["connectors"]["github"]["enabled"] = False
            config.write_text(json.dumps(settings))
            assert await asyncio.wait_for(reader.read(), 4) == b""
            writer.close()
            await writer.wait_closed()
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, ssl=client, server_hostname="server"
            )
            writer.write(b'{"name":"github"}\n')
            await writer.drain()
            denial = await asyncio.wait_for(reader.read(), 3)
            assert b"disabled" in denial
            assert b"private-test-token" not in denial
            assert len(store.reads) == 1
            writer.close()
            await writer.wait_closed()
            assert broker.active == 0
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


@pytest.mark.skipif(not shutil.which("openssl"), reason="TLS test needs openssl")
def test_two_agents_reuse_one_provider_and_workspace_revocation_closes_both(
    tmp_path, monkeypatch
) -> None:
    certificates(tmp_path)
    config = tmp_path / "config.json"
    settings = {
        "workspaces": {"workspace": ["gmail"]},
        "connectors": {"gmail": {"enabled": True, "reference": "shared-gmail"}},
    }
    config.write_text(json.dumps(settings))

    class Store:
        def read(self, reference):
            assert reference == "shared-gmail"
            return {"token": "only-on-broker"}

    from contextlib import contextmanager

    @contextmanager
    def provider(name, credential, write):
        assert name == "gmail" and credential == {"token": "only-on-broker"}
        yield (
            [
                sys.executable,
                "-u",
                "-c",
                "import sys; [print(line.rstrip(), flush=True) for line in sys.stdin]",
            ],
            dict(os.environ),
        )

    monkeypatch.setattr(connector_broker, "provider_command", provider)
    broker = connector_broker.Broker(config, Store())

    async def scenario():
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(tmp_path / "server.crt", tmp_path / "server.key")
        tls.load_verify_locations(tmp_path / "ca.crt")
        tls.verify_mode = ssl.CERT_REQUIRED
        server = await asyncio.start_server(broker.handle, "127.0.0.1", 0, ssl=tls)
        client = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
        client.load_cert_chain(tmp_path / "workspace.crt", tmp_path / "workspace.key")
        streams = []
        try:
            for index in range(2):
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1",
                    server.sockets[0].getsockname()[1],
                    ssl=client,
                    server_hostname="server",
                )
                streams.append((reader, writer))
                writer.write(b'{"name":"gmail"}\n')
                await writer.drain()
                assert json.loads(await asyncio.wait_for(reader.readline(), 3)) == {"ready": True}
                request = (
                    json.dumps({"jsonrpc": "2.0", "id": index, "method": "tools/list"}).encode()
                    + b"\n"
                )
                writer.write(request)
                await writer.drain()
                response = await asyncio.wait_for(reader.readline(), 3)
                assert response == request
                assert b"only-on-broker" not in response
            settings["workspaces"]["workspace"] = []
            config.write_text(json.dumps(settings))
            for reader, _ in streams:
                assert await asyncio.wait_for(reader.read(), 4) == b""
        finally:
            for _, writer in streams:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
