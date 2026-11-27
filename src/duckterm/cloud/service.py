"""HTTP routes of the account service.

Macs sign in with GitHub's device flow and hand the service the token; phones
use the web flow with PKCE, so a one-time code only works in the app that
started it. Either way the service keeps only the GitHub user id and login.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
import secrets
import ssl
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from duckterm.cloud import tickets
from duckterm.cloud.github import GitHub, GitHubError
from duckterm.cloud.store import Store
from duckterm.transport.httpio import parse_request_line, read_body, read_headers, write_response

APP_REDIRECT = "ducky://signed-in"
REQUESTS_PER_MINUTE = 120
SIGN_INS_PER_MINUTE = 10
HEX32 = re.compile(r"[0-9a-f]{32}")
HEX64 = re.compile(r"[0-9a-f]{64}")
BASE64URL = re.compile(r"[A-Za-z0-9_-]{43,128}")

Reply = tuple[int, dict[str, Any], dict[str, str]]


class Refused(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 80
        or any(ord(c) < 32 for c in value)
    ):
        raise Refused(400, "invalid_name")
    return value


def _json(body: bytes, fields: set[str]) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except ValueError as exc:
        raise Refused(400, "invalid_request") from exc
    if not isinstance(value, dict) or set(value) != fields:
        raise Refused(400, "invalid_request")
    return value


class AccountService:
    def __init__(self, store: Store, github: GitHub, public_url: str) -> None:
        self.store = store
        self.github = github
        self.public_url = public_url.rstrip("/")
        self.recent: defaultdict[tuple[str, str], deque[float]] = defaultdict(deque)

    def limited(self, peer: str, bucket: str, limit: int) -> bool:
        now = time.monotonic()
        window = self.recent[(peer, bucket)]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= limit:
            return True
        window.append(now)
        if len(self.recent) > 10000:
            # Drop idle peers so a scan of many addresses can't grow memory without bound.
            for key in [key for key, value in self.recent.items() if not value]:
                del self.recent[key]
        return False

    async def dispatch(
        self, method: str, target: str, headers: dict[str, str], body: bytes, peer: str
    ) -> Reply:
        parsed = urlsplit(target)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        path = parsed.path
        try:
            if self.limited(peer, "all", REQUESTS_PER_MINUTE):
                raise Refused(429, "rate_limited")
            if path.startswith(("/v1/auth/", "/v1/session/")) and self.limited(
                peer, "sign-in", SIGN_INS_PER_MINUTE
            ):
                raise Refused(429, "rate_limited")
            if (method, path) == ("GET", "/v1/auth/github/start"):
                return self.start(query)
            if (method, path) == ("GET", "/v1/auth/github/callback"):
                return await self.callback(query)
            if (method, path) == ("POST", "/v1/session/redeem"):
                request = _json(body, {"code", "verifier", "name"})
                account = self.store.redeem_sign_in(str(request["code"]), str(request["verifier"]))
                if account is None:
                    raise Refused(403, "sign_in_expired")
                return self.signed_in(account, "phone", _name(request["name"]))
            if (method, path) == ("POST", "/v1/session/github"):
                request = _json(body, {"github_token", "name"})
                name = _name(request["name"])
                token = request["github_token"]
                if not isinstance(token, str) or not 0 < len(token) <= 256:
                    raise Refused(400, "invalid_request")
                try:
                    user = await asyncio.to_thread(self.github.user, token)
                finally:
                    await asyncio.to_thread(self.github.revoke, token)
                return self.signed_in(self.store.account(user.id, user.login), "mac", name)
            return self.authenticated(method, path, headers, body)
        except Refused as exc:
            return exc.status, {"error": exc.reason}, {}
        except GitHubError:
            return 502, {"error": "github_unavailable"}, {}
        except ValueError as exc:
            return 409, {"error": str(exc)}, {}

    def start(self, query: dict[str, str]) -> Reply:
        challenge = query.get("challenge", "")
        if not BASE64URL.fullmatch(challenge) or len(challenge) != 43:
            raise Refused(400, "invalid_challenge")
        state = secrets.token_urlsafe(32)
        self.store.start_sign_in(state, challenge)
        redirect = self.github.authorize_url(self.public_url + "/v1/auth/github/callback", state)
        return 302, {}, {"Location": redirect}

    async def callback(self, query: dict[str, str]) -> Reply:
        def back(**fields: str) -> Reply:
            return 302, {}, {"Location": APP_REDIRECT + "?" + urlencode(fields)}

        code, state = query.get("code", ""), query.get("state", "")
        if not code or not state:
            return back(error="cancelled")
        redirect_uri = self.public_url + "/v1/auth/github/callback"
        try:
            token = await asyncio.to_thread(self.github.exchange, code, redirect_uri)
            try:
                user = await asyncio.to_thread(self.github.user, token)
            finally:
                await asyncio.to_thread(self.github.revoke, token)
        except GitHubError:
            # The browser lands back in the app, never on a bare JSON error.
            return back(error="github_unavailable")
        one_time = self.store.finish_sign_in(state, self.store.account(user.id, user.login))
        return back(code=one_time) if one_time else back(error="expired")

    def signed_in(self, account: str, kind: str, name: str) -> Reply:
        device, token = self.store.add_device(account, kind, name)
        login = self.store.login(account)
        return (
            200,
            {"account_id": account, "login": login, "device_id": device, "session_token": token},
            {},
        )

    def authenticated(self, method: str, path: str, headers: dict[str, str], body: bytes) -> Reply:
        authorization = headers.get("authorization", "")
        device = (
            self.store.authenticate(authorization.removeprefix("Bearer "))
            if authorization.startswith("Bearer ")
            else None
        )
        if device is None:
            raise Refused(401, "signed_out")
        account = device["account_id"]
        if (method, path) == ("GET", "/v1/me"):
            return (
                200,
                {
                    "account_id": account,
                    "login": device["login"],
                    "device": {key: device[key] for key in ("id", "kind", "name")},
                },
                {},
            )
        if (method, path) == ("PUT", "/v1/mac"):
            if device["kind"] != "mac":
                raise Refused(403, "mac_only")
            request = _json(body, {"host_id", "certificate_sha256", "tls_name", "url", "name"})
            url = urlsplit(str(request["url"]))
            if (
                not HEX32.fullmatch(str(request["host_id"]))
                or not HEX64.fullmatch(str(request["certificate_sha256"]))
                or not (request["tls_name"] is None or isinstance(request["tls_name"], str))
                or url.scheme != "https"
                or not url.hostname
                or len(str(request["url"])) > 256
            ):
                raise Refused(400, "invalid_mac")
            self.store.db.execute(
                "UPDATE devices SET name = ? WHERE id = ?", (_name(request["name"]), device["id"])
            )
            secret = self.store.register_mac(device["id"], request)
            return 200, {"ticket_secret": base64.urlsafe_b64encode(secret).decode()}, {}
        if (method, path) == ("GET", "/v1/macs"):
            return 200, {"macs": self.store.macs(account)}, {}
        if (method, path) == ("POST", "/v1/tickets"):
            if device["kind"] != "phone":
                raise Refused(403, "phone_only")
            host_id = str(_json(body, {"host_id"})["host_id"])
            signing = self.store.ticket_secret(account, host_id)
            if signing is None:
                raise Refused(404, "mac_not_on_account")
            claims = {
                "account_id": account,
                "login": device["login"],
                "phone_id": device["id"],
                "phone_name": device["name"],
                "host_id": host_id,
            }
            return 200, {"ticket": tickets.mint(signing, claims)}, {}
        if (method, path) == ("GET", "/v1/devices"):
            return 200, {"devices": self.store.devices(account)}, {}
        if method == "DELETE" and path.startswith("/v1/devices/"):
            if not self.store.remove_device(account, path.removeprefix("/v1/devices/")):
                raise Refused(404, "no_such_device")
            return 200, {"removed": True}, {}
        if (method, path) == ("POST", "/v1/session/sign-out"):
            self.store.remove_device(account, device["id"])
            return 200, {"removed": True}, {}
        if (method, path) == ("DELETE", "/v1/account"):
            self.store.remove_account(account)
            return 200, {"removed": True}, {}
        raise Refused(404, "not_found")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        address = peer[0] if peer else "-"
        try:
            async with asyncio.timeout(15):
                method, target = parse_request_line(await reader.readline())
                if not target.startswith("/") or len(target) > 4096:
                    raise ValueError("Invalid target")
                headers = await read_headers(reader)
                body = await read_body(reader, headers, max_bytes=8192)
                status, payload, extra = await self.dispatch(method, target, headers, body, address)
                await write_response(
                    writer,
                    status,
                    json.dumps(payload),
                    {"Cache-Control": "no-store", **extra},
                    "application/json",
                )
        except (TimeoutError, ValueError, asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()


async def serve(
    service: AccountService,
    host: str,
    port: int,
    context: ssl.SSLContext | None = None,
) -> asyncio.Server:
    return await asyncio.start_server(service.handle, host, port, ssl=context, limit=16384)
