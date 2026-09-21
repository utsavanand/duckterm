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


def configured() -> bool:
    return bool(
        os.environ.get("DUCKTERM_HOSTED")
        or os.environ.get("DUCKTERM_CONNECTOR_CLIENT_CONFIG")
        or config_path().is_file()
    )


def config_path() -> Path:
    from duckterm.helpers import paths

    if os.environ.get("DUCKTERM_CONNECTOR_CLIENT_CONFIG"):
        return Path(os.environ["DUCKTERM_CONNECTOR_CLIENT_CONFIG"])
    local = paths.home() / "connector-client.json"
    return CONFIG if os.environ.get("DUCKTERM_HOSTED") else local


def connect(name: str, config: Path | None = None) -> tuple[ssl.SSLSocket, bytes]:
    config = config or config_path()
    settings = json.loads(config.read_text())
    for key in ("server_ca", "certificate", "private_key"):
        value = Path(settings[key]).expanduser()
        settings[key] = str((config.parent / value).resolve())
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


def attach(config: Path) -> None:
    """Enroll a machine once; register all granted connectors for its local harnesses."""
    from duckterm import connectors
    from duckterm.helpers import paths
    from duckterm.helpers.private_files import private_write

    settings = json.loads(config.read_text())
    for key in ("server_ca", "certificate", "private_key"):
        value = Path(settings[key]).expanduser()
        settings[key] = str(
            (config.parent / value).resolve() if not value.is_absolute() else value.resolve()
        )
    # Prove certificate trust and access before replacing an existing enrollment.
    stream, response = connect("status", config)
    with stream:
        data = json.loads(response)
    if not isinstance(data.get("connectors"), list):
        raise RuntimeError("Connector service did not return a catalog")
    private_write(paths.home() / "connector-client.json", json.dumps(settings))
    for row in data["connectors"]:
        if row.get("name") in connectors.NAMES:
            connectors._install(row["name"])
