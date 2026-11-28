"""Account service: GitHub sign-in, Mac registration and phone tickets, with a fake GitHub."""

import asyncio
import base64
import contextlib
import io
import json
import secrets
import ssl
import stat
from urllib.parse import parse_qs, urlsplit

import pytest

import duckterm.cloud.service as service_module
from duckterm.cloud import tickets
from duckterm.cloud.__main__ import tls_context
from duckterm.cloud.github import GitHub, GitHubError, GitHubUser
from duckterm.cloud.service import SIGN_INS_PER_MINUTE, AccountService, serve
from duckterm.cloud.store import Store, challenge_for
from duckterm.mobile.cli import configure

HOST = "a" * 32
PIN = "b" * 64


class FakeGitHub:
    """Tokens map to users; anything else is another app's token or a bad code."""

    def __init__(self):
        self.users = {}
        self.codes = {}
        self.revoked = []

    def authorize_url(self, redirect_uri, state):
        return f"https://github.example/authorize?state={state}&redirect_uri={redirect_uri}"

    def exchange(self, code, redirect_uri):
        if code not in self.codes:
            raise GitHubError("bad code")
        return self.codes[code]

    def user(self, token):
        if token not in self.users:
            raise GitHubError("not this app's token")
        return self.users[token]

    def revoke(self, token):
        self.revoked.append(token)


@pytest.fixture
def cloud(tmp_path):
    github = FakeGitHub()
    store = Store(tmp_path / "accounts.sqlite")
    yield AccountService(store, github, "https://cloud.example"), github
    store.close()


def call(service, method, target, body=None, token=None, peer="198.51.100.1"):
    headers = {"authorization": "Bearer " + token} if token else {}
    data = json.dumps(body).encode() if body is not None else b""
    return asyncio.run(service.dispatch(method, target, headers, data, peer))


def mac_session(service, github, user=None, name="Studio Mac"):
    user = user or GitHubUser(7, "octo")
    token = secrets.token_hex(8)
    github.users[token] = user
    status, answer, _ = call(
        service, "POST", "/v1/session/github", {"github_token": token, "name": name}
    )
    assert status == 200 and token in github.revoked
    return answer


def phone_session(service, github, user=None, name="Utsav's iPhone"):
    user = user or GitHubUser(7, "octo")
    verifier = secrets.token_urlsafe(48)
    status, _, headers = call(
        service, "GET", "/v1/auth/github/start?challenge=" + challenge_for(verifier)
    )
    assert status == 302
    state = parse_qs(urlsplit(headers["Location"]).query)["state"][0]
    github.codes["web-code"] = "web-token"
    github.users["web-token"] = user
    status, _, headers = call(
        service, "GET", f"/v1/auth/github/callback?code=web-code&state={state}"
    )
    back = urlsplit(headers["Location"])
    assert status == 302 and f"{back.scheme}://{back.netloc}" == "ducky://signed-in"
    code = parse_qs(back.query)["code"][0]
    status, answer, _ = call(
        service, "POST", "/v1/session/redeem", {"code": code, "verifier": verifier, "name": name}
    )
    assert status == 200 and "web-token" in github.revoked
    return answer


def register(service, session, host=HOST):
    status, answer, _ = call(
        service,
        "PUT",
        "/v1/mac",
        {
            "host_id": host,
            "certificate_sha256": PIN,
            "tls_name": "studio.local",
            "url": "https://studio.local:8765",
            "name": "Studio Mac",
        },
        session["session_token"],
    )
    assert status == 200
    return base64.urlsafe_b64decode(answer["ticket_secret"])


def test_mac_and_phone_sign_in_to_one_account_and_the_phone_gets_a_ticket(cloud):
    service, github = cloud
    mac = mac_session(service, github)
    secret = register(service, mac)
    phone = phone_session(service, github)
    assert phone["account_id"] == mac["account_id"] and phone["login"] == "octo"

    status, answer, _ = call(service, "GET", "/v1/macs", token=phone["session_token"])
    assert status == 200
    [listed] = answer["macs"]
    assert (listed["host_id"], listed["certificate_sha256"], listed["url"]) == (
        HOST,
        PIN,
        "https://studio.local:8765",
    )
    assert "ticket_secret" not in listed

    status, answer, _ = call(
        service, "POST", "/v1/tickets", {"host_id": HOST}, phone["session_token"]
    )
    claims = tickets.verify(secret, answer["ticket"], HOST)
    assert claims["phone_id"] == phone["device_id"]
    assert claims["phone_name"] == "Utsav's iPhone" and claims["login"] == "octo"
    # Re-registering (new address or pin) keeps the secret the Mac already holds.
    assert register(service, mac) == secret


def test_a_one_time_code_needs_the_starting_apps_verifier_and_works_once(cloud):
    service, github = cloud
    verifier = secrets.token_urlsafe(48)
    _, _, headers = call(
        service, "GET", "/v1/auth/github/start?challenge=" + challenge_for(verifier)
    )
    state = parse_qs(urlsplit(headers["Location"]).query)["state"][0]
    github.codes["c"], github.users["t"] = "t", GitHubUser(1, "someone")
    _, _, headers = call(service, "GET", f"/v1/auth/github/callback?code=c&state={state}")
    code = parse_qs(urlsplit(headers["Location"]).query)["code"][0]
    stolen = {"code": code, "verifier": secrets.token_urlsafe(48), "name": "Thief"}
    assert call(service, "POST", "/v1/session/redeem", stolen)[0] == 403
    # The wrong verifier spent the code, so even the real app must start again.
    genuine = {"code": code, "verifier": verifier, "name": "Phone"}
    assert call(service, "POST", "/v1/session/redeem", genuine)[0] == 403
    # A state can't be finished twice.
    _, _, headers = call(service, "GET", f"/v1/auth/github/callback?code=c&state={state}")
    assert parse_qs(urlsplit(headers["Location"]).query) == {"error": ["expired"]}
    _, _, headers = call(service, "GET", "/v1/auth/github/callback?code=bad&state=" + state)
    assert parse_qs(urlsplit(headers["Location"]).query) == {"error": ["github_unavailable"]}
    assert call(service, "GET", "/v1/auth/github/start?challenge=short")[0] == 400


def test_another_apps_github_token_creates_nothing(cloud):
    service, github = cloud
    status, answer, _ = call(
        service, "POST", "/v1/session/github", {"github_token": "foreign", "name": "Mac"}
    )
    assert (status, answer) == (502, {"error": "github_unavailable"})
    assert "foreign" in github.revoked
    assert service.store.db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_roles_and_accounts_are_kept_apart(cloud):
    service, github = cloud
    mac = mac_session(service, github)
    register(service, mac)
    phone = phone_session(service, github)
    stranger = phone_session(service, github, user=GitHubUser(99, "stranger"))
    # A phone can't register itself as a Mac, and a Mac can't mint phone tickets.
    assert call(service, "PUT", "/v1/mac", {}, phone["session_token"])[0] == 403
    assert call(service, "POST", "/v1/tickets", {"host_id": HOST}, mac["session_token"])[0] == 403
    # Another account sees no Macs and can't get a ticket for this one.
    assert call(service, "GET", "/v1/macs", token=stranger["session_token"])[1] == {"macs": []}
    assert (
        call(service, "POST", "/v1/tickets", {"host_id": HOST}, stranger["session_token"])[0] == 404
    )
    assert (
        call(service, "DELETE", "/v1/devices/" + mac["device_id"], token=stranger["session_token"])[
            0
        ]
        == 404
    )
    assert call(service, "GET", "/v1/me", token="not-a-session")[0] == 401


def test_removing_a_device_or_the_account_signs_out_everywhere_it_should(cloud):
    service, github = cloud
    mac = mac_session(service, github)
    register(service, mac)
    phone = phone_session(service, github)
    status, answer, _ = call(service, "GET", "/v1/devices", token=mac["session_token"])
    assert [device["kind"] for device in answer["devices"]] == ["mac", "phone"]
    assert (
        call(service, "DELETE", "/v1/devices/" + phone["device_id"], token=mac["session_token"])[0]
        == 200
    )
    assert call(service, "GET", "/v1/me", token=phone["session_token"])[0] == 401
    assert call(service, "DELETE", "/v1/account", token=mac["session_token"])[0] == 200
    assert call(service, "GET", "/v1/me", token=mac["session_token"])[0] == 401
    for table in ("accounts", "devices", "macs"):
        assert service.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_sign_in_attempts_are_rate_limited_per_address(cloud):
    service, _ = cloud
    for _ in range(SIGN_INS_PER_MINUTE):
        call(service, "GET", "/v1/auth/github/start?challenge=x", peer="203.0.113.5")
    assert call(service, "GET", "/v1/auth/github/start?challenge=x", peer="203.0.113.5")[0] == 429
    assert call(service, "GET", "/v1/auth/github/start?challenge=x", peer="203.0.113.6")[0] == 400


def test_tickets_reject_forgery_expiry_and_another_mac():
    secret = secrets.token_bytes(32)
    claims = {
        "account_id": "a",
        "login": "octo",
        "phone_id": "p",
        "phone_name": "Phone",
        "host_id": HOST,
    }
    ticket = tickets.mint(secret, claims, now=1000)
    assert tickets.verify(secret, ticket, HOST, now=1000)["phone_id"] == "p"
    with pytest.raises(ValueError, match="expired"):
        tickets.verify(secret, ticket, HOST, now=1000 + tickets.TICKET_SECONDS)
    with pytest.raises(ValueError, match="another Mac"):
        tickets.verify(secret, ticket, "c" * 32, now=1000)
    with pytest.raises(ValueError, match="signature"):
        tickets.verify(secrets.token_bytes(32), ticket, HOST, now=1000)
    body, signature = ticket.split(".")
    forged = json.loads(base64.urlsafe_b64decode(body + "=="))
    forged["phone_name"] = "Attacker"
    tampered = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    with pytest.raises(ValueError):
        tickets.verify(secret, tampered + "." + signature, HOST, now=1000)


def test_tickets_carry_a_nonce_and_audience_and_have_one_spelling():
    secret = secrets.token_bytes(32)
    claims = {
        "account_id": "a",
        "login": "octo",
        "phone_id": "p",
        "phone_name": "Phone",
        "host_id": HOST,
    }
    first, second = (tickets.mint(secret, claims, now=1000) for _ in range(2))
    verified = tickets.verify(secret, first, HOST, now=1000)
    assert verified["aud"] == "ducky-pair"
    assert verified["nonce"] != tickets.verify(secret, second, HOST, now=1000)["nonce"]
    body, signature = first.split(".")
    # Padding, foreign characters and flipped unused low bits all decoded to the
    # same signature before; each is now a different, refused ticket.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    flipped = alphabet[alphabet.index(signature[-1]) ^ 1]
    for variant in (first + "=", first + "!!", f"{body}.{signature[:-1]}{flipped}"):
        with pytest.raises(ValueError, match="Malformed"):
            tickets.verify(secret, variant, HOST, now=1000)
    other = json.loads(base64.urlsafe_b64decode(body + "=="))
    other["aud"] = "something-else"
    resigned = tickets._encode(json.dumps(other).encode())
    signed = tickets._encode(tickets.hmac.new(secret, resigned.encode(), "sha256").digest())
    with pytest.raises(ValueError, match="Malformed"):
        tickets.verify(secret, f"{resigned}.{signed}", HOST, now=1000)


def test_redeem_needs_an_rfc_7636_verifier(cloud):
    service, _ = cloud
    for verifier in ("x", "a" * 42, "a" * 129, "a" * 42 + "!"):
        request = {"code": "c", "verifier": verifier, "name": "Phone"}
        assert call(service, "POST", "/v1/session/redeem", request) == (
            400,
            {"error": "invalid_verifier"},
            {},
        )


def test_account_database_and_its_journal_are_private(tmp_path):
    path = tmp_path / "cloud.sqlite"
    store = Store(path)
    try:
        store.account(7, "octo")
        for name in ("cloud.sqlite", "cloud.sqlite-wal", "cloud.sqlite-shm"):
            assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600, name
    finally:
        store.close()


def test_service_refuses_to_start_without_tls_unless_told(tmp_path):
    with pytest.raises(SystemExit, match="DUCKY_CLOUD_CERT"):
        tls_context({})
    with pytest.raises(SystemExit):
        tls_context({"DUCKY_CLOUD_CERT": str(tmp_path / "cert.pem")})
    assert tls_context({"DUCKY_CLOUD_PLAINTEXT": "1"}) is None
    configure(tmp_path, "127.0.0.1", 4430)
    context = tls_context(
        {
            "DUCKY_CLOUD_CERT": str(tmp_path / "ducky-cert.pem"),
            "DUCKY_CLOUD_KEY": str(tmp_path / "ducky-key.pem"),
        }
    )
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_mac_registration_limits_the_tls_name(cloud):
    service, github = cloud
    mac = mac_session(service, github)
    request = {
        "host_id": HOST,
        "certificate_sha256": PIN,
        "tls_name": "a" * 254,
        "url": "https://studio.local:8765",
        "name": "Studio Mac",
    }
    assert call(service, "PUT", "/v1/mac", request, mac["session_token"])[0] == 400
    request["tls_name"] = "a" * 253
    assert call(service, "PUT", "/v1/mac", request, mac["session_token"])[0] == 200


def test_errors_reach_the_client_as_fixed_codes(cloud, monkeypatch):
    service, github = cloud
    monkeypatch.setattr("duckterm.cloud.store.MAX_DEVICES", 1)
    mac_session(service, github)
    token = secrets.token_hex(8)
    github.users[token] = GitHubUser(7, "octo")
    request = {"github_token": token, "name": "Second Mac"}
    assert call(service, "POST", "/v1/session/github", request) == (
        409,
        {"error": "too_many_devices"},
        {},
    )
    request = {
        "host_id": HOST,
        "certificate_sha256": PIN,
        "tls_name": None,
        "url": "https://[broken:8765",
        "name": "Studio Mac",
    }
    first = service.store.db.execute("SELECT id FROM devices").fetchone()["id"]
    service.store.db.execute("DELETE FROM devices WHERE id = ?", (first,))
    mac = mac_session(service, github)
    assert call(service, "PUT", "/v1/mac", request, mac["session_token"]) == (
        400,
        {"error": "invalid_request"},
        {},
    )


def test_a_non_json_github_answer_is_a_github_error(monkeypatch):
    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "duckterm.cloud.github.urllib.request.urlopen", lambda *a, **k: Answer(b"<html>")
    )
    with pytest.raises(GitHubError):
        GitHub("client", "secret").exchange("code", "https://cloud.example/cb")


def test_connections_are_capped_and_counted_before_their_headers(cloud, monkeypatch):
    service, _ = cloud
    monkeypatch.setattr(service_module, "HEADER_SECONDS", 0.3)
    monkeypatch.setattr(service_module, "CONNECTIONS_PER_PEER", 2)
    monkeypatch.setattr(service_module, "REQUESTS_PER_MINUTE", 4)

    async def closed(reader):
        # Closing with unread input resets the socket, which is also a close.
        try:
            return await asyncio.wait_for(reader.read(), 2) == b""
        except ConnectionError:
            return True

    async def run():
        listener = await serve(service, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        try:
            # Headers that never finish lose the connection after HEADER_SECONDS.
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /v1/me HTTP/1.1\r\n")
            for index in range(5):
                with contextlib.suppress(ConnectionError):
                    writer.write(f"X-Slow-{index}: 1\r\n".encode())
                await asyncio.sleep(0.1)
            assert await closed(reader)
            writer.close()
            # Two silent connections fill this address's share; a third is refused.
            held = [await asyncio.open_connection("127.0.0.1", port) for _ in range(2)]
            await asyncio.sleep(0.05)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            assert await closed(reader)
            writer.close()
            for _, held_writer in held:
                held_writer.close()
            await asyncio.sleep(0.4)
            # Four connections arrived, none with complete headers, and the
            # minute's budget is spent anyway.
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /v1/me HTTP/1.1\r\nHost: x\r\n\r\n")
            assert await closed(reader)
            writer.close()
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(run())


def test_silent_tcp_is_capped_and_dropped_before_any_tls_handshake(cloud, tmp_path, monkeypatch):
    service, _ = cloud
    monkeypatch.setattr(service_module, "HEADER_SECONDS", 0.5)
    monkeypatch.setattr(service_module, "CONNECTIONS_PER_PEER", 4)
    configure(tmp_path, "127.0.0.1", 4430)
    context = tls_context(
        {
            "DUCKY_CLOUD_CERT": str(tmp_path / "ducky-cert.pem"),
            "DUCKY_CLOUD_KEY": str(tmp_path / "ducky-key.pem"),
        }
    )
    client = ssl.create_default_context()
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE

    async def closed_within(reader, seconds):
        try:
            return await asyncio.wait_for(reader.read(), seconds) == b""
        except ConnectionError:
            return True

    async def run():
        listener = await serve(service, "127.0.0.1", 0, context)
        port = listener.sockets[0].getsockname()[1]
        try:
            # A real TLS client still gets an answer.
            reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=client)
            writer.write(b"GET /v1/me HTTP/1.1\r\nHost: x\r\n\r\n")
            assert (await reader.readline()).startswith(b"HTTP/1.1 401")
            writer.close()
            await asyncio.sleep(0.05)
            # Plain TCP that never sends a ClientHello counts against the cap.
            held = [await asyncio.open_connection("127.0.0.1", port) for _ in range(4)]
            await asyncio.sleep(0.05)
            assert service.open["127.0.0.1"] == 4
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            assert await closed_within(reader, 0.2)
            writer.close()
            # And loses the connection when the header budget runs out.
            for held_reader, _ in held:
                assert await closed_within(held_reader, 1.5)
            assert not service.open
            for _, held_writer in held:
                held_writer.close()
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(run())
