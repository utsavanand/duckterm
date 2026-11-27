"""Short-lived tickets saying "this phone belongs to the Mac's account".

The account service signs a ticket with a secret it shares with one Mac. The
Mac checks it before showing the phone as pending; approval is still the
owner's click on the Mac.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

TICKET_SECONDS = 300
FIELDS = {"version", "account_id", "login", "phone_id", "phone_name", "host_id", "expires_at"}


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(secret: bytes, claims: dict[str, Any], *, now: int | None = None) -> str:
    now = int(time.time()) if now is None else now
    body = _encode(
        json.dumps(
            {**claims, "version": 1, "expires_at": now + TICKET_SECONDS}, sort_keys=True
        ).encode()
    )
    signature = hmac.new(secret, body.encode(), hashlib.sha256).digest()
    return body + "." + _encode(signature)


def verify(secret: bytes, ticket: str, host_id: str, *, now: int | None = None) -> dict[str, Any]:
    """The ticket's claims, or ValueError when it is forged, expired or for another Mac."""
    now = int(time.time()) if now is None else now
    if len(ticket) > 2048 or ticket.count(".") != 1:
        raise ValueError("Malformed account ticket")
    body, signature = ticket.split(".")
    expected = hmac.new(secret, body.encode(), hashlib.sha256).digest()
    try:
        presented = _decode(signature)
    except ValueError as exc:
        raise ValueError("Malformed account ticket") from exc
    if not hmac.compare_digest(expected, presented):
        raise ValueError("Account ticket signature mismatch")
    try:
        claims = json.loads(_decode(body))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Malformed account ticket") from exc
    if not isinstance(claims, dict) or set(claims) != FIELDS or claims["version"] != 1:
        raise ValueError("Malformed account ticket")
    if claims["host_id"] != host_id:
        raise ValueError("Account ticket is for another Mac")
    if not isinstance(claims["expires_at"], int) or claims["expires_at"] <= now:
        raise ValueError("Account ticket expired")
    return claims
