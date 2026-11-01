"""One local invitation ceremony, with an explicit owner decision at its end."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
import select
import shutil
import socket
import ssl
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from duckterm.helpers import instance
from duckterm.helpers.private_files import private_read, private_write
from duckterm.mobile import network
from duckterm.mobile.cli import address, configure, enable, invitation_link, invitation_payload
from duckterm.mobile.grants import Grants
from duckterm.mobile.qr import render_terminal_qr


@contextlib.contextmanager
def pairing_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / ".ducky-pair.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another Ducky pairing is already open for this workspace") from exc
        yield
    finally:
        os.close(fd)


def prepare(
    directory: Path, *, selected_address: str | None = None, port: int | None = None
) -> dict[str, Any]:
    """Create only a missing identity; existing pins and approved grants survive."""
    raw = private_read(directory / "ducky-host.json")
    current = json.loads(raw) if raw else None
    if current is not None and (not isinstance(current, dict) or current.get("version") != 1):
        raise ValueError("Unsupported Ducky host configuration")
    if port is not None and not 1024 <= port <= 65535:
        raise ValueError("Ducky port must be between 1024 and 65535")
    if selected_address is not None:
        selected_address = address(selected_address)
    candidates: list[network.AddressCandidate] = []
    try:
        candidates = network.active_candidates()
    except ValueError:
        if selected_address is None:
            # Existing explicit loopback/non-macOS configurations still work.
            if current and not current.get("interface"):
                selected_address = address(current["address"])
            else:
                raise
    if selected_address is None:
        if current and current.get("interface"):
            selected_address = network.detect_address(
                interface=current["interface"], family=current["family"]
            )
        elif current and ipaddress.ip_address(current["address"]).is_loopback:
            selected_address = address(current["address"])
        else:
            # Do not select a preferred candidate when several ports/families exist.
            selected_address = network.detect_address()
    matches = [item for item in candidates if item.address == selected_address]
    if len(matches) > 1:
        raise ValueError(
            "That address occurs on multiple interfaces; choose a unique private address"
        )
    candidate = matches[0] if matches else None
    hostname = network.local_hostname() if candidate else None
    if current and current.get("hostname") and hostname != current["hostname"]:
        raise ValueError("This Mac's Bonjour name changed; restore its name before pairing")
    selected_port = (
        port if port is not None else (current["port"] if current else instance.port() + 130)
    )
    if current is None:
        configure(
            directory,
            selected_address,
            selected_port,
            hostname=hostname,
            interface=candidate.interface if candidate else None,
        )
    else:
        # Preserve the original certificate and key, including legacy CA:TRUE
        # identities. A new DNS locator can retain its original IP TLS name.
        if hostname and not current.get("hostname"):
            current["hostname"] = hostname
            current["tls_name"] = address(current["address"])
        current.update(address=selected_address, port=selected_port)
        if candidate and hostname:
            current.update(interface=candidate.interface, family=candidate.family)
        else:
            current.pop("interface", None)
            current.pop("family", None)
        private_write(directory / "ducky-host.json", json.dumps(current))
        enable(directory)
    saved: dict[str, Any] = json.loads(private_read(directory / "ducky-host.json") or "{}")
    return saved


def ready(directory: Path, config: dict[str, Any]) -> bool:
    """Check the dedicated listener with its pinned TLS identity, without owner auth."""
    raw = private_read(directory / "ducky-status.json")
    status = json.loads(raw) if raw else {}
    if status.get("state") != "ready" or status.get("port") != config["port"]:
        return False
    host = address(status["address"])
    certificate = private_read(directory / "ducky-cert.pem")
    if certificate is None:
        return False
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
    if status.get("certificate_sha256") != pin:
        return False
    identity = config.get("tls_name") or config.get("hostname") or config["address"]
    context = ssl.create_default_context(cadata=certificate)
    with (
        socket.create_connection((host, config["port"]), timeout=1) as plain,
        context.wrap_socket(plain, server_hostname=identity) as connection,
    ):
        peer = connection.getpeercert(binary_form=True)
        return peer is not None and hashlib.sha256(peer).hexdigest() == pin


def wait_for_listener(directory: Path, config: dict[str, Any], *, timeout: float = 12) -> None:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            if ready(directory, config):
                return
        except (OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(0.2)
    raise ValueError(
        "Ducky could not start. Open the updated Duckterm app and check its network address"
    )


def read_decision(expires_at: int) -> str:
    remaining = max(0, expires_at - time.time())
    if remaining == 0 or not select.select([sys.stdin], [], [], remaining)[0]:
        return ""
    return sys.stdin.readline().strip().lower()


def pair(directory: Path, *, selected_address: str | None = None, port: int | None = None) -> int:
    # No --yes flag or piped input: approval belongs to an interactive owner.
    if not sys.stdin.isatty():
        raise ValueError(
            "Run duckterm ducky pair in an interactive terminal to approve your iPhone"
        )
    with pairing_lock(directory):
        config = prepare(directory, selected_address=selected_address, port=port)
        print("Starting Ducky…", flush=True)
        wait_for_listener(directory, config)
        payload = invitation_payload(directory)
        grants = Grants(directory)
        try:
            link = invitation_link(payload)
            encoding = (sys.stdout.encoding or "").lower().replace("-", "")
            qr = render_terminal_qr(
                link,
                columns=shutil.get_terminal_size().columns,
                is_tty=sys.stdout.isatty()
                and os.environ.get("TERM") != "dumb"
                and encoding in {"utf8", "utf_8"},
            )
            if qr:
                print("Scan with your iPhone camera.\n" + qr)
            else:
                print("Open this invitation on your iPhone (widen the terminal to show a QR code).")
            print(link, flush=True)
            print("Waiting for your iPhone. Invitation expires in 10 minutes.", flush=True)
            while time.time() < payload["expires_at"]:
                pending = grants.pending_invitation(payload["invite"])
                if pending:
                    # Device names originate on the network. ASCII escaping
                    # prevents terminal control or bidi sequences in prompts.
                    print(f"\nDevice ID: {pending['id']}")
                    print(
                        f"{ascii(pending['name'])} wants to view your agents — approve? [y/N] ",
                        end="",
                        flush=True,
                    )
                    decision = read_decision(min(payload["expires_at"], pending["expires_at"]))
                    if decision in {"y", "yes"}:
                        grants.approve_invitation(payload["invite"], pending["id"])
                        print("Read-only access approved.")
                        return 0
                    print("Pairing declined.")
                    return 1
                time.sleep(0.25)
            print("Invitation expired. Run duckterm ducky pair to try again.")
            return 1
        except KeyboardInterrupt:
            print("\nPairing cancelled.")
            return 130
        finally:
            grants.cancel_invitation(payload["invite"])
