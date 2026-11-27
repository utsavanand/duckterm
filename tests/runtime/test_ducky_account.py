"""Mac side of Ducky accounts against the real account service and a fake GitHub."""

import asyncio
import hashlib
import json
import secrets
import ssl
from urllib.parse import urlsplit

import pytest
from tests.unit.test_cloud_accounts import FakeGitHub, phone_session

from duckterm.cloud.github import GitHubUser
from duckterm.cloud.service import AccountService
from duckterm.cloud.store import Store
from duckterm.editions import LITE, EditionConfig
from duckterm.mobile import account
from duckterm.mobile.account import AccountError, matching_code
from duckterm.mobile.bridge import Bridge, start
from duckterm.mobile.cli import configure
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "state.sqlite", edition=LITE)
    yield Server(history=history, edition=LITE), tmp_path
    history.close()


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    github = FakeGitHub()
    store = Store(tmp_path / "cloud.sqlite")
    monkeypatch.setenv("DUCKY_GITHUB_CLIENT_ID", "client")
    monkeypatch.setenv("DUCKY_CLOUD_URL", "https://cloud.example")
    yield AccountService(store, github, "https://cloud.example"), github
    store.close()


def routed(service, github, user):
    """Account-module fetches: GitHub's device flow answered here, the service in-process."""
    polls = []

    def call(method, url, body, headers):
        if url == "https://github.com/login/device/code":
            return {
                "device_code": "dev",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
            }
        if url == "https://github.com/login/oauth/access_token":
            polls.append(body)
            if len(polls) == 1:
                return {"error": "authorization_pending"}
            github.users["device-token"] = user
            return {"access_token": "device-token"}
        parsed = urlsplit(url)
        assert f"{parsed.scheme}://{parsed.netloc}" == "https://cloud.example"
        lowered = {key.lower(): value for key, value in headers.items()}
        status, answer, _ = asyncio.run(
            service.dispatch(method, parsed.path, lowered, json.dumps(body).encode(), "mac")
        )
        if status >= 400:
            raise AccountError(answer["error"])
        return answer

    return call


def remote_edition(server):
    server.edition = EditionConfig(server.edition.name, server.edition.features | {"ducky_remote"})


def test_signed_in_mac_lets_its_accounts_phone_wait_for_approval_with_a_matching_code(app, cloud):
    server, root = app
    service, github = cloud
    configure(root, "127.0.0.1", 4430)
    remote_edition(server)
    said = []
    user = GitHubUser(7, "octo")
    mac = account.sign_in(
        root, "Studio Mac", say=said.append, call=routed(service, github, user), sleep=lambda _: 0
    )
    assert said == ["Enter WDJB-MJHT at https://github.com/login/device to sign in with GitHub."]
    assert mac["login"] == "octo" and "device-token" in github.revoked
    stored = (root / "ducky-account.json").read_text()
    assert "device-token" not in stored
    assert (root / "ducky-account.json").stat().st_mode & 0o777 == 0o600
    pin = hashlib.sha256(
        ssl.PEM_cert_to_DER_cert((root / "ducky-cert.pem").read_text())
    ).hexdigest()

    phone = phone_session(service, github, user=user)
    _, listed, _ = asyncio.run(
        service.dispatch(
            "GET", "/v1/macs", {"authorization": "Bearer " + phone["session_token"]}, b"", "p"
        )
    )
    [studio] = listed["macs"]
    assert studio["certificate_sha256"] == pin and studio["url"] == "https://127.0.0.1:4430"
    _, issued, _ = asyncio.run(
        service.dispatch(
            "POST",
            "/v1/tickets",
            {"authorization": "Bearer " + phone["session_token"]},
            json.dumps({"host_id": studio["host_id"]}).encode(),
            "p",
        )
    )
    bridge = Bridge(server, root)
    token = secrets.token_urlsafe(32)

    async def claim(ticket, credential):
        body = json.dumps({"ticket": ticket, "token": credential}).encode()
        return await bridge.dispatch("POST", "/v1/pair/account", {}, body)

    async def run():
        code, pending = await claim(issued["ticket"], token)
        assert code == 202 and pending["status"] == "pending"
        [device] = bridge.grants.devices()
        assert device["name"] == "Utsav's iPhone" and device["account"]["login"] == "octo"
        # The phone computes the same code from its own token and the pin it was given.
        assert device["code"] == matching_code(token, studio["certificate_sha256"])
        # A retry with the same credential is the same claim; a leaked ticket with
        # another credential can't add a look-alike while the phone waits.
        assert (await claim(issued["ticket"], token))[1]["id"] == pending["id"]
        assert (await claim(issued["ticket"], secrets.token_urlsafe(32)))[0] == 403
        headers = {"authorization": "Ducky " + token}
        assert (await bridge.dispatch("GET", "/v2/snapshot", headers, b""))[0] == 403
        bridge.grants.approve(pending["id"])
        assert (await bridge.dispatch("GET", "/v2/snapshot", headers, b""))[0] == 200
        assert "code" not in bridge.grants.devices()[0]
        assert (await claim("forged.ticket", secrets.token_urlsafe(32)))[0] == 403

    asyncio.run(run())


def test_account_claims_need_the_flag_and_a_signed_in_mac(app, cloud):
    server, root = app
    configure(root, "127.0.0.1", 4430)
    bridge = Bridge(server, root)
    body = json.dumps({"ticket": "a.b", "token": secrets.token_urlsafe(32)}).encode()

    async def run():
        assert (await bridge.dispatch("POST", "/v1/pair/account", {}, body))[0] == 404
        remote_edition(server)
        assert (await bridge.dispatch("POST", "/v1/pair/account", {}, body))[0] == 403

    asyncio.run(run())
    assert bridge.grants.devices() == []


def test_sign_out_forgets_the_account_even_when_the_service_is_unreachable(app, cloud):
    server, root = app
    service, github = cloud
    configure(root, "127.0.0.1", 4430)
    account.sign_in(
        root,
        "Studio Mac",
        say=lambda _: None,
        call=routed(service, github, GitHubUser(7, "octo")),
        sleep=lambda _: 0,
    )

    def unreachable(*_args):
        raise AccountError("Can't reach the sign-in service")

    account.sign_out(root, call=unreachable)
    assert not (root / "ducky-account.json").exists()
    with pytest.raises(AccountError, match="isn't signed in"):
        account.verify_ticket(root, "a.b", "c" * 32)


def _signed_in_mac_and_phone(server, root, service, github):
    configure(root, "127.0.0.1", 4430)
    remote_edition(server)
    user = GitHubUser(7, "octo")
    account.sign_in(
        root,
        "Studio Mac",
        say=lambda _: None,
        call=routed(service, github, user),
        sleep=lambda _: 0,
    )
    phone = phone_session(service, github, user=user)
    host_id = json.loads((root / "ducky-account.json").read_text())["host_id"]

    def ticket():
        _, issued, _ = asyncio.run(
            service.dispatch(
                "POST",
                "/v1/tickets",
                {"authorization": "Bearer " + phone["session_token"]},
                json.dumps({"host_id": host_id}).encode(),
                "p",
            )
        )
        return issued["ticket"]

    return Bridge(server, root), ticket


def test_a_ticket_makes_one_claim_and_a_phone_appears_once(app, cloud):
    server, root = app
    service, github = cloud
    bridge, ticket = _signed_in_mac_and_phone(server, root, service, github)
    first = ticket()

    def claim(value, token=None, peer="192.0.2.1"):
        body = json.dumps({"ticket": value, "token": token or secrets.token_urlsafe(32)})
        return asyncio.run(
            bridge.dispatch("POST", "/v1/pair/account", {}, body.encode(), peer=peer)
        )

    code, pending = claim(first)
    assert code == 202
    bridge.grants.approve(pending["id"])
    # Allowed: neither the same ticket nor a fresh one adds a second entry for this phone.
    assert claim(first)[0] == 403
    assert claim(ticket())[0] == 403
    assert [device["status"] for device in bridge.grants.devices()] == ["active"]
    # Removed: the spent ticket stays spent, in every spelling; a new ticket works.
    bridge.grants.revoke(pending["id"])
    for replay in (first, first + "=", first + "!!"):
        assert claim(replay)[0] == 403
    assert bridge.grants.devices() == []
    assert claim(ticket())[0] == 202


def test_forged_account_claims_spend_only_their_own_addresss_budget(app, cloud):
    server, root = app
    service, github = cloud
    bridge, ticket = _signed_in_mac_and_phone(server, root, service, github)

    def claim(value, peer):
        body = json.dumps({"ticket": value, "token": secrets.token_urlsafe(32)})
        return asyncio.run(
            bridge.dispatch("POST", "/v1/pair/account", {}, body.encode(), peer=peer)
        )[0]

    attempts = [claim("forged.ticket", "203.0.113.9") for _ in range(25)]
    assert attempts == [403] * 10 + [429] * 15
    # Another address, and QR pairing, still have their whole budgets.
    assert claim(ticket(), "192.0.2.1") == 202
    invite = json.dumps({"invite": "x", "token": secrets.token_urlsafe(32), "name": "Phone"})
    assert asyncio.run(bridge.dispatch("POST", "/v1/pair", {}, invite.encode()))[0] == 403


def test_account_claim_bodies_are_small(app):
    server, root = app
    configure(root, "127.0.0.1", 4430)
    remote_edition(server)
    config = json.loads((root / "ducky-bridge.json").read_text())
    config["port"] = 0
    (root / "ducky-bridge.json").write_text(json.dumps(config))

    async def run():
        bridge = await start(server)
        port = bridge.listener.sockets[0].getsockname()[1]
        context = ssl.create_default_context(cafile=str(root / "ducky-cert.pem"))
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, ssl=context, server_hostname="127.0.0.1"
            )
            writer.write(
                b"POST /v1/pair/account HTTP/1.1\r\nHost: x\r\nContent-Length: 4097\r\n\r\n"
            )
            await writer.drain()
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            assert head.split()[1] == b"413"
            writer.close()
        finally:
            await bridge.close()

    asyncio.run(run())


def test_the_account_service_address_must_be_https(monkeypatch):
    monkeypatch.setenv("DUCKY_CLOUD_URL", "http://cloud.example")
    with pytest.raises(AccountError, match="https"):
        account.cloud_url()
    monkeypatch.setenv("DUCKY_CLOUD_URL", "https://cloud.example/")
    assert account.cloud_url() == "https://cloud.example"
