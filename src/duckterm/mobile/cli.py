"""Local owner controls for the invited read-only phone pilot."""

import argparse
import base64
import hashlib
import ipaddress
import json
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from duckterm.helpers import instance
from duckterm.helpers.private_files import private_read, private_write
from duckterm.mobile.grants import Grants


def address(value: str) -> str:
    ip = ipaddress.ip_address(value)
    networks = (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "fc00::/7",
        "::1/128",
    )
    if not any(ip in ipaddress.ip_network(network) for network in networks):
        raise ValueError("Use this Mac's private network IP or loopback address")
    return str(ip)


def configure(directory: Path, host: str, port: int, *, replace: bool = False) -> None:
    host = address(host)
    if not 1024 <= port <= 65535:
        raise ValueError("Ducky port must be between 1024 and 65535")
    if (directory / "ducky-cert.pem").exists() and not replace:
        raise ValueError(
            "This host already has a Ducky identity; use enable to resume it, or "
            "setup --replace to change its address/certificate and require phones to pair again"
        )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # OpenSSL only creates the local TLS identity. The running server remains
    # Python stdlib; neither generation nor use contacts an external service.
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        root = Path(temporary)
        config = root / "openssl.cnf"
        config.write_text(
            f"[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=Ducky\n[ext]\nsubjectAltName=IP:{host}\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n"
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-nodes",
                "-days",
                "365",
                "-config",
                str(config),
                "-keyout",
                str(root / "key.pem"),
                "-out",
                str(root / "cert.pem"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        # Generate successfully before invalidating working access. After this
        # point any interrupted replacement stays disabled until retried.
        (directory / "ducky-bridge.json").unlink(missing_ok=True)
        if replace:
            Grants(directory).reset()
        private_write(directory / "ducky-key.pem", (root / "key.pem").read_text())
        private_write(directory / "ducky-cert.pem", (root / "cert.pem").read_text())
    private_write(
        directory / "ducky-host.json", json.dumps({"version": 1, "address": host, "port": port})
    )
    enable(directory)


def enable(directory: Path) -> None:
    raw = private_read(directory / "ducky-host.json")
    if not raw:
        raise ValueError("Run duckterm ducky setup first")
    config = json.loads(raw)
    address(config["address"])
    private_write(directory / "ducky-bridge.json", raw)


def invitation(directory: Path) -> str:
    raw = private_read(directory / "ducky-bridge.json")
    if not raw:
        raise ValueError("Ducky is disabled; enable it before creating an invitation")
    config = json.loads(raw)
    host = address(config["address"])
    certificate = ssl.PEM_cert_to_DER_cert((directory / "ducky-cert.pem").read_text())
    payload = {
        "version": 1,
        **Grants(directory).invite(),
        "url": f"https://{'[' + host + ']' if ':' in host else host}:{config['port']}",
        "certificate_sha256": hashlib.sha256(certificate).hexdigest(),
    }
    return "ducky://pair?invite=" + base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).decode().rstrip("=")


def add_parser(sub: Any) -> None:
    parser = sub.add_parser("ducky", help="configure invited read-only phone access")
    commands = parser.add_subparsers(dest="ducky_command", required=True)
    setup = commands.add_parser(
        "setup", help="create a local TLS identity and enable the phone bridge"
    )
    setup.add_argument("--address", required=True, help="this Mac's private network IP")
    setup.add_argument("--port", type=int, default=4430)
    setup.add_argument(
        "--replace",
        action="store_true",
        help="replace the TLS identity/address and revoke all phones and invitations",
    )
    for name in ("enable", "disable", "invite", "devices"):
        commands.add_parser(name)
    for name in ("approve", "revoke"):
        commands.add_parser(name).add_argument("device_id")


def run(args: argparse.Namespace) -> int:
    directory = instance.home()
    grants = Grants(directory)
    try:
        if args.ducky_command == "setup":
            configure(directory, args.address, args.port, replace=args.replace)
            if args.replace:
                print("Ducky identity replaced. Previous phones and invitations are revoked.")
                print("Restart Duckterm, create a new invitation, and pair your iPhone again.")
            else:
                print("Ducky enabled. Restart Duckterm to start its separate TLS listener.")
        elif args.ducky_command == "enable":
            enable(directory)
            print("Ducky enabled. Restart Duckterm to start the listener.")
        elif args.ducky_command == "disable":
            (directory / "ducky-bridge.json").unlink(missing_ok=True)
            print("Ducky access disabled. Restart Duckterm to close the TLS listener.")
        elif args.ducky_command == "invite":
            print(invitation(directory))
            print(
                "Private, single-use invitation; expires in ten minutes. "
                "Review duckterm ducky devices and approve the phone's matching ID.",
                file=sys.stderr,
            )
        elif args.ducky_command == "devices":
            print(json.dumps(grants.devices(), indent=2))
        elif args.ducky_command == "approve":
            grants.approve(args.device_id)
            print("Read-only folder and session status access approved.")
        elif args.ducky_command == "revoke":
            grants.revoke(args.device_id)
            print("Device access revoked.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"duckterm: {exc}", file=sys.stderr)
        return 1
