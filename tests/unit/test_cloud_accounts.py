"""Account service: GitHub sign-in, Mac registration and phone tickets, with a fake GitHub."""

import asyncio
import base64
import json
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest

from duckterm.cloud import tickets
from duckterm.cloud.github import GitHubError, GitHubUser
from duckterm.cloud.service import SIGN_INS_PER_MINUTE, AccountService
from duckterm.cloud.store import Store, challenge_for

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
