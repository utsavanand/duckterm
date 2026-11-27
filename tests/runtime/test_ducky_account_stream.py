"""The Mac app's account ceremonies over a real pipe and real grants, no network."""

import json
import secrets
from functools import partial

import pytest
from tests.runtime.test_ducky_pairing_stream import pipe  # noqa: F401

from duckterm.helpers.private_files import private_write
from duckterm.mobile import account, account_stream
from duckterm.mobile.account import matching_code


@pytest.fixture
def frames(monkeypatch):
    events = []
    reactions = []

    def emit(state, **fields):
        events.append({"state": state, **fields})
        if reactions:
            reactions.pop(0)()

    monkeypatch.setattr(account_stream, "emit", emit)
    monkeypatch.setattr(account_stream, "WATCH_SECONDS", 0.01)
    return events, reactions


def claim(grants, name="Utsav's iPhone", phone_id="phone-1"):
    token = secrets.token_urlsafe(32)
    code = matching_code(token, "a" * 64)
    claims = {"login": "octo", "phone_id": phone_id, "phone_name": name}
    return grants.claim_account(token, claims, code), token, code


def test_watcher_reports_sign_in_and_each_waiting_phone_until_the_app_closes(
    tmp_path, pipe, frames  # noqa: F811
):
    events, reactions = frames
    grants = account_stream.Grants(tmp_path)
    waiting = {}

    def sign_in_and_claim():
        private_write(tmp_path / account.ACCOUNT_FILE, json.dumps({"login": "octo"}))
        # A QR invitation's pending phone is not an account claim and stays out.
        grants.claim(grants.invite()["invite"], secrets.token_urlsafe(32), "QR phone")
        waiting["claim"], _, waiting["code"] = claim(grants)

    reactions[:] = [sign_in_and_claim, pipe.close]
    assert account_stream.claims_json(tmp_path) == 0
    assert events[0] == {"state": "claims", "login": None, "devices": []}
    [phone] = events[1]["devices"]
    assert events[1]["login"] == "octo"
    assert phone == {
        "id": waiting["claim"]["id"],
        "name": "Utsav's iPhone",
        "expires_at": phone["expires_at"],
        "login": "octo",
        "code": waiting["code"],
    }
    assert events[2:] == [{"state": "cancelled"}]


@pytest.mark.parametrize("action", ["approve", "deny"])
def test_owner_allows_or_denies_one_waiting_phone(tmp_path, pipe, frames, action):  # noqa: F811
    events, reactions = frames
    grants = account_stream.Grants(tmp_path)
    pending, token, code = claim(grants)
    command = {"action": action, "device_id": pending["id"]}
    if action == "approve":
        command["capabilities"] = ["conversation:read", "session:send", "status:read"]
    reactions[:] = [lambda: pipe.send(json.dumps(command).encode() + b"\n")]
    assert account_stream.claim_json(tmp_path, pending["id"]) == 0
    assert events[0]["state"] == "pending" and events[0]["device"]["code"] == code
    if action == "approve":
        assert events[1]["state"] == "approved"
        assert events[1]["device"]["capabilities"] == sorted(command["capabilities"])
        assert grants.authenticate(token)["capabilities"] == sorted(command["capabilities"])
    else:
        assert events[1] == {
            "state": "denied",
            "device": {"id": pending["id"], "name": "Utsav's iPhone"},
        }
        assert grants.devices() == []


def test_claim_window_never_offers_a_qr_phone_or_a_phone_removed_while_open(
    tmp_path, pipe, frames  # noqa: F811
):
    events, reactions = frames
    grants = account_stream.Grants(tmp_path)
    qr = grants.claim(grants.invite()["invite"], secrets.token_urlsafe(32), "QR phone")
    assert account_stream.claim_json(tmp_path, qr["id"]) == 1
    assert events == [{"state": "expired", "phase": "pending"}]

    events.clear()
    pending, _, _ = claim(grants, phone_id="phone-2")
    reactions[:] = [lambda: grants.revoke(pending["id"])]
    assert account_stream.claim_json(tmp_path, pending["id"]) == 1
    assert events[-1] == {"state": "error", "code": "approval_unavailable"}


def test_closing_the_window_cancels_sign_in_and_service_failures_have_fixed_codes(
    tmp_path, pipe, frames, monkeypatch  # noqa: F811
):
    events, reactions = frames
    monkeypatch.setenv("DUCKY_GITHUB_CLIENT_ID", "client")

    def github(method, url, body, headers):
        if url.endswith("/device/code"):
            return {
                "device_code": "d",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
            }
        return {"error": "authorization_pending"}

    monkeypatch.setattr(account, "sign_in", partial(account.sign_in, call=github))
    reactions[:] = [pipe.close]
    assert account_stream.signin_json(tmp_path, "Studio Mac") == 130
    assert events == [
        {
            "state": "code",
            "user_code": "WDJB-MJHT",
            "verification_uri": "https://github.com/login/device",
        },
        {"state": "cancelled"},
    ]
    assert not (tmp_path / account.ACCOUNT_FILE).exists()

    events.clear()
    monkeypatch.setenv("DUCKY_GITHUB_CLIENT_ID", "")
    assert account_stream.signin_json(tmp_path, "Studio Mac") == 1
    assert events == [{"state": "error", "code": "signin_unavailable"}]
