"""Capability approval and worker checks use only disposable private grant stores."""

import json
import secrets
import threading
import time

import pytest

from duckterm.helpers.private_files import private_write
from duckterm.mobile.grants import Denied, Grants


@pytest.fixture
def phone(tmp_path):
    grants = Grants(tmp_path)
    invitation = grants.invite()
    token = secrets.token_urlsafe(32)
    device = grants.claim(invitation["invite"], token, "Fixture phone")
    return grants, invitation, token, device


def test_legacy_and_default_approvals_keep_only_status_permission(phone):
    grants, _, token, device = phone
    assert grants.authenticate(token, pending=True)["capabilities"] == []
    grants.approve(device["id"])
    state = json.loads(grants.path.read_text())
    state["devices"][device["id"]].pop("capabilities")
    private_write(grants.path, json.dumps(state))
    public = grants.authenticate(token)
    assert public["capabilities"] == ["status:read"]
    assert public["api_version"] == 2
    assert "token_hash" not in public and token not in json.dumps(public)
    assert grants.devices()[0]["capabilities"] == ["status:read"]
    for scope in ("conversation:read", "session:send"):
        with pytest.raises(Denied):
            grants.authenticated_by_id(device["id"], scope)
    assert grants.authenticated_by_id(device["id"], "status:read")["id"] == device["id"]


@pytest.mark.parametrize(
    "scopes", [[], ["conversation:read"], ["session:send"], ["conversation:read", "session:send"]]
)
@pytest.mark.parametrize("by_invitation", [False, True])
def test_only_explicit_independent_scopes_are_approved(phone, scopes, by_invitation):
    grants, invitation, token, device = phone
    if by_invitation:
        grants.approve_invitation(invitation["invite"], device["id"], capabilities=scopes)
    else:
        grants.approve(device["id"], capabilities=scopes)
    expected = sorted({"status:read", *scopes})
    assert grants.authenticate(token)["capabilities"] == expected
    for scope in ("conversation:read", "session:send"):
        if scope in scopes:
            public = grants.authenticated_by_id(device["id"], scope, host_id=device["host_id"])
            assert public["capabilities"] == expected
            assert "token_hash" not in public
        else:
            with pytest.raises(Denied):
                grants.authenticated_by_id(device["id"], scope)


@pytest.mark.parametrize(
    "scopes",
    [
        "session:send",
        {},
        True,
        [None],
        [["session:send"]],
        ["session:send", "future:admin"],
        ["session:send", "session:send"],
    ],
)
@pytest.mark.parametrize("by_invitation", [False, True])
def test_malformed_or_unknown_approval_never_mutates_grants(phone, scopes, by_invitation):
    grants, invitation, _, device = phone
    before = grants.path.read_bytes()
    with pytest.raises(ValueError, match="Invalid Ducky capabilities"):
        if by_invitation:
            grants.approve_invitation(invitation["invite"], device["id"], capabilities=scopes)
        else:
            grants.approve(device["id"], capabilities=scopes)
    assert grants.path.read_bytes() == before


@pytest.mark.parametrize(
    "scopes",
    [
        None,
        "session:send",
        {},
        [True],
        ["session:send", "future:admin"],
        ["session:send", "session:send"],
    ],
)
def test_damaged_persisted_scopes_cannot_keep_partial_write_authority(phone, scopes):
    grants, _, token, device = phone
    grants.approve(device["id"], capabilities=["session:send"])
    state = json.loads(grants.path.read_text())
    state["devices"][device["id"]]["capabilities"] = scopes
    private_write(grants.path, json.dumps(state))
    assert grants.authenticate(token)["capabilities"] == ["status:read"]
    with pytest.raises(Denied):
        grants.authenticated_by_id(device["id"], "session:send")


def test_worker_checks_pending_revoked_host_and_unknown_capability(phone):
    grants, _, token, device = phone
    with pytest.raises(Denied):
        grants.authenticated_by_id(device["id"], "status:read")
    grants.approve(device["id"], capabilities=["session:send"])
    with pytest.raises(Denied, match="host identity"):
        grants.authenticated_by_id(device["id"], "session:send", host_id="wrong-host")
    with pytest.raises(Denied):
        grants.authenticated_by_id(device["id"], "admin")
    public = grants.authenticated_by_id(device["id"], "session:send")
    public["capabilities"].append("conversation:read")
    assert "conversation:read" not in grants.authenticate(token)["capabilities"]
    grants.revoke(device["id"])
    with pytest.raises(Denied):
        grants.authenticated_by_id(device["id"], "session:send")


def test_worker_recheck_has_bounded_lock_and_preserves_store(phone):
    grants, _, _, device = phone
    grants.approve(device["id"], capabilities=["session:send"])
    before = grants.path.read_bytes()
    with grants.locked():
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="grant store is busy"):
            grants.authenticated_by_id(device["id"], "session:send", lock_timeout=0.04)
        assert time.monotonic() - started < 1
    assert grants.path.read_bytes() == before
    assert grants.authenticated_by_id(device["id"], "session:send")["id"] == device["id"]


def test_scoped_operation_orders_revocation_and_always_releases_lock(phone):
    grants, _, _, device = phone
    grants.approve(device["id"], capabilities=["session:send"])
    entered, revoked = threading.Event(), threading.Event()

    def revoke():
        entered.set()
        grants.revoke(device["id"])
        revoked.set()

    thread = threading.Thread(target=revoke, daemon=True)
    try:
        with (
            pytest.raises(RuntimeError, match="operation failed"),
            grants.authorized_device(device["id"], "session:send") as public,
        ):
            thread.start()
            assert entered.wait(1)
            assert not revoked.wait(0.04)
            assert public["capabilities"] == ["session:send", "status:read"]
            assert "token_hash" not in json.dumps(public)
            raise RuntimeError("operation failed")
        assert revoked.wait(1)
        with pytest.raises(Denied):
            grants.authenticated_by_id(device["id"], "session:send")
    finally:
        if thread.ident is not None:
            thread.join(timeout=1)


def test_expired_or_unrelated_invitation_cannot_grant_capabilities(phone):
    grants, invitation, token, device = phone
    second = grants.invite()
    before = grants.path.read_bytes()
    with pytest.raises(Denied):
        grants.approve_invitation(second["invite"], device["id"], capabilities=["session:send"])
    with pytest.raises(Denied):
        grants.approve_invitation(
            invitation["invite"],
            device["id"],
            now=invitation["expires_at"],
            capabilities=["session:send"],
        )
    assert grants.path.read_bytes() == before
    assert grants.authenticate(token, pending=True)["capabilities"] == []
