"""This Mac's DuckTerm account: GitHub sign-in, registration and phone tickets.

Signing in lets a phone on the same account find this Mac without a QR code.
It never approves a phone: an account claim arrives as a pending device that
the owner allows on this Mac, comparing the matching code on both screens.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from duckterm.cloud import tickets
from duckterm.helpers.private_files import private_read, private_write

CLOUD_URL = "https://relay.duckterm.utsava.xyz"
# The public client id of DuckTerm's GitHub OAuth app. Device flow needs no secret.
GITHUB_CLIENT_ID = ""
ACCOUNT_FILE = "ducky-account.json"

Fetch = Callable[[str, str, dict[str, Any] | None, dict[str, str]], dict[str, Any]]


class AccountError(ValueError):
    def __init__(self, message: str, code: str = "service_unavailable") -> None:
        super().__init__(message)
        # A fixed code the Mac app shows; the message can carry the service's words.
        self.code = code


def matching_code(token: str, certificate_sha256: str) -> str:
    """Four digits both screens show. The cloud never sees the token, so a server
    that swapped in another Mac's certificate would produce a different code."""
    raw = hashlib.sha256(f"ducky-match:{certificate_sha256}:{token}".encode()).digest()
    return f"{int.from_bytes(raw[:4], 'big') % 10000:04d}"


def fetch(method: str, url: str, body: dict[str, Any] | None, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None, method=method
    )
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read(65536) or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            reason = json.loads(exc.read(4096)).get("error", exc.code)
        except ValueError:
            reason = exc.code
        raise AccountError(f"Sign-in service refused the request ({reason})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AccountError("Can't reach the sign-in service") from exc


def load(directory: Path) -> dict[str, Any] | None:
    raw = private_read(directory / ACCOUNT_FILE)
    return json.loads(raw) if raw else None


def cloud_url() -> str:
    url = os.environ.get("DUCKY_CLOUD_URL", CLOUD_URL).rstrip("/")
    # The session token and ticket secret travel to this address.
    if not url.startswith("https://"):
        raise AccountError("DUCKY_CLOUD_URL must be an https:// address")
    return url


def sign_in(
    directory: Path,
    name: str,
    *,
    say: Callable[[str, str], None] = lambda code, uri: print(
        f"Enter {code} at {uri} to sign in with GitHub."
    ),
    call: Fetch = fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """GitHub's device flow, then a DuckTerm session for this Mac."""
    client_id = os.environ.get("DUCKY_GITHUB_CLIENT_ID", GITHUB_CLIENT_ID)
    if not client_id:
        raise AccountError("Ducky sign-in isn't available in this build yet", "signin_unavailable")
    start = call("POST", "https://github.com/login/device/code", {"client_id": client_id}, {})
    say(start["user_code"], start["verification_uri"])
    interval = max(5, int(start.get("interval", 5)))
    deadline = time.monotonic() + int(start.get("expires_in", 900))
    while time.monotonic() < deadline:
        sleep(interval)
        answer = call(
            "POST",
            "https://github.com/login/oauth/access_token",
            {
                "client_id": client_id,
                "device_code": start["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            {},
        )
        if token := answer.get("access_token"):
            break
        error = answer.get("error")
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            raise AccountError("GitHub sign-in was cancelled or expired", "signin_expired")
    else:
        raise AccountError("GitHub sign-in expired; try again", "signin_expired")
    session = call(
        "POST", cloud_url() + "/v1/session/github", {"github_token": token, "name": name}, {}
    )
    account = {
        "cloud_url": cloud_url(),
        "account_id": session["account_id"],
        "login": session["login"],
        "device_id": session["device_id"],
        "session_token": session["session_token"],
    }
    private_write(directory / ACCOUNT_FILE, json.dumps(account))
    return register(directory, name, call=call)


def register(directory: Path, name: str, *, call: Fetch = fetch) -> dict[str, Any]:
    """Publish this Mac's address and certificate pin; keep the ticket secret it returns."""
    account = load(directory)
    if account is None:
        raise AccountError("Sign in first: duckterm ducky signin")
    raw = private_read(directory / "ducky-bridge.json")
    if not raw:
        raise AccountError("Turn on Ducky first: duckterm ducky pair", "setup_unavailable")
    from duckterm.mobile.cli import address, local_name

    config = json.loads(raw)
    host = local_name(config["hostname"]) if config.get("hostname") else address(config["address"])
    certificate = ssl.PEM_cert_to_DER_cert((directory / "ducky-cert.pem").read_text())
    from duckterm.mobile.grants import Grants

    # Writing persists the host id a fresh grant store would otherwise only invent.
    with Grants(directory).locked(write=True) as state:
        host_id = state["host_id"]
    answer = call(
        "PUT",
        account["cloud_url"] + "/v1/mac",
        {
            "host_id": host_id,
            "certificate_sha256": hashlib.sha256(certificate).hexdigest(),
            "tls_name": config.get("tls_name"),
            "url": f"https://{'[' + host + ']' if ':' in host else host}:{config['port']}",
            "name": name,
        },
        {"Authorization": "Bearer " + account["session_token"]},
    )
    account["ticket_secret"] = answer["ticket_secret"]
    account["host_id"] = host_id
    private_write(directory / ACCOUNT_FILE, json.dumps(account))
    return account


def sign_out(directory: Path, *, call: Fetch = fetch) -> None:
    """Forget the account here, and sign this Mac out of the service when it answers."""
    account = load(directory)
    (directory / ACCOUNT_FILE).unlink(missing_ok=True)
    if account is not None:
        # The local copy is gone either way; an unreachable service's session expires unused.
        with contextlib.suppress(AccountError):
            call(
                "POST",
                account["cloud_url"] + "/v1/session/sign-out",
                {},
                {"Authorization": "Bearer " + account["session_token"]},
            )


def verify_ticket(directory: Path, ticket: str, host_id: str) -> dict[str, Any]:
    account = load(directory)
    if account is None or "ticket_secret" not in account or account.get("host_id") != host_id:
        raise AccountError("This Mac isn't signed in to a DuckTerm account")
    return tickets.verify(base64.urlsafe_b64decode(account["ticket_secret"]), ticket, host_id)
