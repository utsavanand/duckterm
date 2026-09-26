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


def test_simultaneous_handshakes_reserve_capacity_atomically(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "workspaces": {"workspace": ["github"]},
                "connectors": {"github": {"enabled": True, "reference": "synthetic"}},
            }
        )
    )
    broker = connector_broker.Broker(config, FakeStore())

    class Writer:
        def __init__(self):
            self.data = b""

        def get_extra_info(self, key):
            return {"subject": ((("commonName", "workspace"),),)}

        def write(self, data):
            self.data += data

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def scenario():
        gate = asyncio.Event()

        async def fetch(*args):
            await gate.wait()
            raise ValueError("synthetic credential-store outage")

        monkeypatch.setattr(connector_broker.asyncio, "to_thread", fetch)
        readers = [asyncio.StreamReader() for _ in range(connector_broker.MAX_CONNECTIONS + 1)]
        writers = [Writer() for _ in readers]
        tasks = [
            asyncio.create_task(broker.handle(r, w)) for r, w in zip(readers, writers, strict=True)
        ]
        await asyncio.sleep(0)
        for reader in readers:
            reader.feed_data(b'{"name":"github"}\n')
        for _ in range(10):
            await asyncio.sleep(0)
        assert broker.active == connector_broker.MAX_CONNECTIONS
        assert sum(b"error" in w.data for w in writers) == 1
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(b'{"name":"status"}\n')
        await broker.handle(reader, writer)
        assert b'"connectors"' in writer.data  # status remains available at capacity
        gate.set()
        await asyncio.gather(*tasks)
        assert broker.active == 0

    asyncio.run(scenario())


def test_rotation_outage_and_restart_close_old_streams(tmp_path, monkeypatch):
    certificates(tmp_path)
    config = tmp_path / "config.json"
    settings = {
        "workspaces": {"workspace": ["github"]},
        "connectors": {"github": {"enabled": True, "reference": "v1"}},
    }
    config.write_text(json.dumps(settings))

    class Store:
        outage = False

        def read(self, reference):
            if self.outage:
                raise OSError("synthetic secret service outage")
            return {"token": reference}

    store = Store()
    monkeypatch.setattr(
        connector_broker,
        "command",
        lambda *args: (
            [
                sys.executable,
                "-u",
                "-c",
                "import sys; [print(x.rstrip(),flush=True) for x in sys.stdin]",
            ],
            dict(os.environ),
        ),
    )

    async def scenario():
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(tmp_path / "server.crt", tmp_path / "server.key")
        tls.load_verify_locations(tmp_path / "ca.crt")
        tls.verify_mode = ssl.CERT_REQUIRED
        client = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
        client.load_cert_chain(tmp_path / "workspace.crt", tmp_path / "workspace.key")
        broker = connector_broker.Broker(config, store)
        listener = await asyncio.start_server(broker.handle, "127.0.0.1", 0, ssl=tls)
        port = listener.sockets[0].getsockname()[1]
        writers = []

        async def connect():
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, ssl=client, server_hostname="server"
            )
            writers.append(writer)
            writer.write(b'{"name":"github"}\n')
            await writer.drain()
            return reader, writer, json.loads(await asyncio.wait_for(reader.readline(), 4))

        try:
            reader, _, response = await connect()
            assert response == {"ready": True}
            settings["connectors"]["github"]["reference"] = "v2"
            config.write_text(json.dumps(settings))
            assert await asyncio.wait_for(reader.read(), 4) == b""
            reader, writer, response = await connect()
            assert response == {"ready": True}
            writer.write(b"new identity\n")
            await writer.drain()
            assert await reader.readline() == b"new identity\n"
            writer.close()
            await writer.wait_closed()
            store.outage = True
            _, _, response = await connect()
            assert "error" in response and "outage" not in json.dumps(response)
            store.outage = False
            listener.close()
            await listener.wait_closed()
            listener = await asyncio.start_server(
                connector_broker.Broker(config, store).handle, "127.0.0.1", port, ssl=tls
            )
            _, writer, response = await connect()
            assert response == {"ready": True}
            writer.close()
        finally:
            for writer in writers:
                writer.close()
                with __import__("contextlib").suppress(OSError):
                    await writer.wait_closed()
            listener.close()
            await listener.wait_closed()
            await asyncio.sleep(0.3)

    asyncio.run(scenario())
