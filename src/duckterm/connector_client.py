"""Workspace-side MCP relay. Holds execution credentials, never provider tokens."""

import contextlib
import json
import os
import socket
import ssl
import sys
import threading
from pathlib import Path
from typing import Any

CONFIG = Path("/etc/duckterm/connector-client.json")


def connect(name: str, config: Path = CONFIG) -> tuple[ssl.SSLSocket, bytes]:
    settings = json.loads(config.read_text())
    tls = ssl.create_default_context(cafile=settings["server_ca"])
    tls.load_cert_chain(settings["certificate"], settings["private_key"])
    raw = socket.create_connection((settings["host"], settings.get("port", 8443)), timeout=15)
    stream = None
    try:
        stream = tls.wrap_socket(raw, server_hostname=settings["server_name"])
        stream.sendall(json.dumps({"name": name}).encode() + b"\n")
        # Read only the handshake line. Never consume the first MCP message.
        response = bytearray()
        while len(response) <= 65536:
            byte = stream.recv(1)
            if not byte:
                raise RuntimeError("Connector service disconnected during setup")
            response.extend(byte)
            if byte == b"\n":
                stream.settimeout(None)
                return stream, bytes(response)
        raise RuntimeError("Invalid connector service response")
    except BaseException:
        if stream is not None:
            stream.close()
        raw.close()
        raise


def statuses() -> list[dict[str, Any]]:
    stream, response = connect("status")
    with stream:
        rows = json.loads(response).get("connectors")
        if not isinstance(rows, list):
            raise RuntimeError("Connector status unavailable")
        return rows


def run(name: str) -> None:
    stream, response = connect(name)
    with stream:
        if json.loads(response).get("ready") is not True:
            raise SystemExit(
                "Connector unavailable or disabled; contact the workspace administrator"
            )

        def send() -> None:
            try:
                while data := os.read(sys.stdin.fileno(), 65536):
                    stream.sendall(data)
            except (OSError, ValueError):
                pass
            finally:
                with contextlib.suppress(OSError):
                    stream.shutdown(socket.SHUT_WR)

        threading.Thread(target=send, daemon=True).start()
        while data := stream.recv(65536):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    raise SystemExit(0)
