"""Pairing ceremonies use scratch identities and real grants, never an owner app."""

import contextlib
import copy
import fcntl
import hashlib
import json
import os
import pty
import secrets
import select
import ssl
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from duckterm.helpers.private_files import private_write
from duckterm.mobile import grants as grants_module
from duckterm.mobile import network, pairing
from duckterm.mobile.cli import configure, invitation_payload
from duckterm.mobile.grants import Denied, Grants, digest


class Clock:
    def __init__(self):
        self.now = 10_000

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    clock = Clock()
    candidates = [network.AddressCandidate("en0", "192.168.12.8")]
    monkeypatch.setattr(network, "active_candidates", lambda: list(candidates))
    monkeypatch.setattr(network, "local_hostname", lambda: "test-mac.local")
    monkeypatch.setattr(pairing.instance, "port", lambda: 43210)
    monkeypatch.setattr(pairing.time, "time", clock.time)
    monkeypatch.setattr(pairing.time, "sleep", clock.sleep)

    def no_real_network(*args, **kwargs):
        raise AssertionError("Pairing tests must not inspect or connect to the owner network")

    monkeypatch.setattr(network, "_read", no_real_network)
    monkeypatch.setattr(pairing.socket, "create_connection", no_real_network)
    return clock, candidates


def state(directory):
    return json.loads((directory / "ducky-grants.json").read_text())


def pin(directory):
    certificate = (directory / "ducky-cert.pem").read_text()
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()


def pending_phone(grants, name="Synthetic iPhone"):
    invite = grants.invite()
    token = secrets.token_urlsafe(32)
    device = grants.claim(invite["invite"], token, name)
    return invite, token, device


def test_first_prepare_creates_private_identity_and_enables_listener(tmp_path):
    root = tmp_path / "scratch-home"
    config = pairing.prepare(root)
    assert config == {
        "version": 1,
        "address": "192.168.12.8",
        "port": 43340,
        "hostname": "test-mac.local",
        "interface": "en0",
        "family": 4,
    }
    assert (root / "ducky-host.json").read_bytes() == (root / "ducky-bridge.json").read_bytes()
    assert root.stat().st_mode & 0o777 == 0o700
    for name in ("ducky-key.pem", "ducky-cert.pem", "ducky-host.json", "ducky-bridge.json"):
        assert (root / name).stat().st_mode & 0o777 == 0o600
    assert "PRIVATE KEY" in (root / "ducky-key.pem").read_text()
    payload = invitation_payload(root)
    assert payload["url"] == "https://test-mac.local:43340"
    assert payload["certificate_sha256"] == pin(root)
    assert "tls_name" not in payload


def test_prepare_reuses_identity_and_existing_approved_device_when_reenabled(
    tmp_path, isolated_runtime
):
    _, candidates = isolated_runtime
    original = pairing.prepare(tmp_path, port=43341)
    grants = Grants(tmp_path)
    _, token, device = pending_phone(grants)
    grants.approve(device["id"])
    before_grants = (tmp_path / "ducky-grants.json").read_bytes()
    before_identity = [
        (tmp_path / name).read_bytes() for name in ("ducky-cert.pem", "ducky-key.pem")
    ]
    (tmp_path / "ducky-bridge.json").unlink()
    candidates[:] = [network.AddressCandidate("en0", "192.168.12.29")]
    updated = pairing.prepare(tmp_path)
    assert updated == {**original, "address": "192.168.12.29"}
    assert (tmp_path / "ducky-grants.json").read_bytes() == before_grants
    assert [
        (tmp_path / name).read_bytes() for name in ("ducky-cert.pem", "ducky-key.pem")
    ] == before_identity
    assert json.loads((tmp_path / "ducky-bridge.json").read_text()) == updated
    assert grants.authenticate(token)["id"] == device["id"]


def test_explicit_private_override_selects_one_of_several_active_ports(tmp_path, isolated_runtime):
    _, candidates = isolated_runtime
    candidates.append(network.AddressCandidate("en5", "10.1.2.3"))
    config = pairing.prepare(tmp_path, selected_address="10.1.2.3", port=43342)
    assert config["address"] == "10.1.2.3"
    assert config["interface"] == "en5"
    assert config["family"] == 4
    assert config["port"] == 43342


def test_explicit_private_override_can_be_used_when_discovery_is_unavailable(tmp_path, monkeypatch):
    def unavailable():
        raise ValueError("Synthetic unsupported platform")

    monkeypatch.setattr(network, "active_candidates", unavailable)
    config = pairing.prepare(tmp_path, selected_address="fd12::5", port=43343)
    assert config == {"version": 1, "address": "fd12::5", "port": 43343}
    payload = invitation_payload(tmp_path)
    assert payload["url"] == "https://[fd12::5]:43343"


@pytest.mark.parametrize("address", ["8.8.8.8", "169.254.1.2", "100.64.0.1", "2001:db8::1"])
def test_nonprivate_override_refused_before_creating_any_identity(address, tmp_path):
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match="private network IP or loopback"):
        pairing.prepare(root, selected_address=address)
    assert not root.exists()


def test_ambiguous_auto_detection_refused_before_creating_any_identity(tmp_path, isolated_runtime):
    _, candidates = isolated_runtime
    candidates.append(network.AddressCandidate("en5", "10.1.2.3"))
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match="Multiple.*--address"):
        pairing.prepare(root)
    assert not root.exists()


def test_explicit_address_on_multiple_ports_is_still_ambiguous(tmp_path, isolated_runtime):
    _, candidates = isolated_runtime
    candidates.append(network.AddressCandidate("en5", "192.168.12.8"))
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match="multiple interfaces"):
        pairing.prepare(root, selected_address="192.168.12.8")
    assert not root.exists()


def test_legacy_ip_identity_gains_local_locator_without_changing_tls_pin(tmp_path):
    configure(tmp_path, "192.168.12.7", 43344)
    before = {name: (tmp_path / name).read_bytes() for name in ("ducky-cert.pem", "ducky-key.pem")}
    original_pin = pin(tmp_path)
    grants = Grants(tmp_path)
    _, token, device = pending_phone(grants)
    grants.approve(device["id"])
    original_grants = (tmp_path / "ducky-grants.json").read_bytes()
    config = pairing.prepare(tmp_path)
    assert config["address"] == "192.168.12.8"
    assert config["hostname"] == "test-mac.local"
    assert config["tls_name"] == "192.168.12.7"
    assert config["interface"] == "en0"
    assert config["family"] == 4
    assert (tmp_path / "ducky-grants.json").read_bytes() == original_grants
    assert {name: (tmp_path / name).read_bytes() for name in before} == before
    payload = invitation_payload(tmp_path)
    assert payload["url"] == "https://test-mac.local:43344"
    assert payload["tls_name"] == "192.168.12.7"
    assert payload["certificate_sha256"] == original_pin
    assert grants.authenticate(token)["status"] == "active"


def test_legacy_loopback_configuration_stays_usable_without_new_fields(tmp_path):
    configure(tmp_path, "127.0.0.1", 43345)
    original_pin = pin(tmp_path)
    config = pairing.prepare(tmp_path)
    assert config == {"version": 1, "address": "127.0.0.1", "port": 43345}
    assert pin(tmp_path) == original_pin


def test_ip_only_relocations_keep_original_tls_identity_without_bonjour(tmp_path, isolated_runtime):
    _, candidates = isolated_runtime
    candidates.clear()
    configure(tmp_path, "192.168.12.7", 43346)
    identity = {
        name: (tmp_path / name).read_bytes() for name in ("ducky-cert.pem", "ducky-key.pem")
    }
    original_pin = pin(tmp_path)
    grants = Grants(tmp_path)
    _, token, device = pending_phone(grants)
    grants.approve(device["id"])
    approved_grants = (tmp_path / "ducky-grants.json").read_bytes()
    for destination in ("192.168.12.99", "10.1.2.3", "fd12::5"):
        config = pairing.prepare(tmp_path, selected_address=destination)
        assert config["address"] == destination
        assert config["tls_name"] == "192.168.12.7"
        assert not {"hostname", "interface", "family"} & config.keys()
        assert {name: (tmp_path / name).read_bytes() for name in identity} == identity
        assert (tmp_path / "ducky-grants.json").read_bytes() == approved_grants
    payload = invitation_payload(tmp_path)
    assert payload["url"] == "https://[fd12::5]:43346"
    assert payload["tls_name"] == "192.168.12.7"
    assert payload["certificate_sha256"] == original_pin
    assert grants.authenticate(token)["id"] == device["id"]


def test_changed_bonjour_name_cannot_silently_change_existing_identity(tmp_path, monkeypatch):
    pairing.prepare(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    monkeypatch.setattr(network, "local_hostname", lambda: "other-mac.local")
    with pytest.raises(ValueError, match="Bonjour name changed"):
        pairing.prepare(tmp_path)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == before


@pytest.mark.parametrize("stdout_tty", [True, False])
def test_noninteractive_pairing_creates_no_files_or_identity(stdout_tty, tmp_path, monkeypatch):
    monkeypatch.setattr(
        pairing,
        "sys",
        SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: False),
            stdout=SimpleNamespace(isatty=lambda: stdout_tty),
        ),
    )
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match="interactive terminal"):
        pairing.pair(root)
    assert not root.exists()


@pytest.fixture
def decision_pipe(monkeypatch):
    reader, writer = os.pipe()
    monkeypatch.setattr(
        pairing, "sys", SimpleNamespace(stdin=SimpleNamespace(fileno=lambda: reader))
    )
    try:
        yield reader, writer
    finally:
        for fd in (reader, writer):
            with contextlib.suppress(OSError):
                os.close(fd)


@pytest.mark.parametrize(
    ("line", "decision"),
    [
        (b" YES \n", "yes"),
        (b"n\n", "n"),
        (b"", ""),
        (b"y", ""),
        (b"y\nn\n", "y"),
        (b"y" + b" " * 127 + b"\n", ""),
    ],
)
def test_timed_decision_requires_a_complete_bounded_line_and_restores_flags(
    line, decision, decision_pipe
):
    reader, writer = decision_pipe
    flags = fcntl.fcntl(reader, fcntl.F_GETFL)
    if line:
        os.write(writer, line)
    os.close(writer)
    assert pairing.read_decision(10_037) == decision
    assert fcntl.fcntl(reader, fcntl.F_GETFL) == flags


def test_expired_terminal_decision_never_reads_input(monkeypatch, decision_pipe):
    def no_read(*args):
        raise AssertionError("An expired invitation must not wait for input")

    reader, _ = decision_pipe
    flags = fcntl.fcntl(reader, fcntl.F_GETFL)
    monkeypatch.setattr(pairing.select, "select", no_read)
    assert pairing.read_decision(10_000) == ""
    assert fcntl.fcntl(reader, fcntl.F_GETFL) == flags


def test_terminal_timeout_never_reads_or_approves_input(monkeypatch, decision_pipe):
    def no_read(*args):
        raise AssertionError("Timed-out input must not be consumed")

    reader, _ = decision_pipe
    flags = fcntl.fcntl(reader, fcntl.F_GETFL)
    monkeypatch.setattr(pairing.select, "select", lambda *args: ([], [], []))
    monkeypatch.setattr(pairing.os, "read", no_read)
    assert pairing.read_decision(10_037) == ""
    assert fcntl.fcntl(reader, fcntl.F_GETFL) == flags


def test_terminal_interrupt_restores_original_descriptor_flags(monkeypatch, decision_pipe):
    reader, writer = decision_pipe
    flags = fcntl.fcntl(reader, fcntl.F_GETFL)
    os.write(writer, b"y\n")

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(pairing.os, "read", interrupt)
    with pytest.raises(KeyboardInterrupt):
        pairing.read_decision(10_037)
    assert fcntl.fcntl(reader, fcntl.F_GETFL) == flags


def test_terminal_deadline_is_monotonic_and_capped_at_ten_minutes(monkeypatch, decision_pipe):
    reader, writer = decision_pipe
    os.write(writer, b"y")
    ticks = iter((100, 100, 701))
    monkeypatch.setattr(pairing.time, "monotonic", lambda: next(ticks))
    waits = []

    def readable(readers, writers, errors, timeout):
        waits.append(timeout)
        return readers, [], []

    monkeypatch.setattr(pairing.select, "select", readable)
    flags = fcntl.fcntl(reader, fcntl.F_GETFL)
    assert pairing.read_decision(13_600) == ""
    assert waits == [600]
    assert fcntl.fcntl(reader, fcntl.F_GETFL) == flags


def test_real_owned_pty_partial_eof_cannot_extend_approval_deadline(tmp_path):
    # y + Ctrl-D produces a readable partial canonical line, but no newline.
    # An outer subprocess timeout makes the old select + readline hang bounded.
    script = """
import fcntl, json, sys, time
from duckterm.mobile.pairing import read_decision
fd = sys.stdin.fileno()
flags = fcntl.fcntl(fd, fcntl.F_GETFL)
print('ready', flush=True)
started = time.monotonic()
decision = read_decision(time.time() + 0.5)
print(json.dumps({'decision': decision, 'elapsed': time.monotonic() - started,
                  'flags_restored': fcntl.fcntl(fd, fcntl.F_GETFL) == flags,
                  'stdin_tty': sys.stdin.isatty()}), flush=True)
"""
    master, slave = pty.openpty()
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
            env={
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONPATH": str(Path(pairing.__file__).resolve().parents[2]),
                "PYTHONDONTWRITEBYTECODE": "1",
                "DUCKTERM_HOME": str(tmp_path / "scratch-home"),
                "DUCKTERM_NO_TERMINAL": "1",
                "DUCKTERM_SUMMARIZER": "off",
            },
            start_new_session=True,
        )
        assert select.select([process.stdout], [], [], 3)[0], "Owned PTY reader did not start"
        assert process.stdout.readline() == "ready\n"
        os.write(master, b"y\x04")
        output, errors = process.communicate(timeout=4)
        assert process.returncode == 0, errors
        result = json.loads(output)
        assert result["stdin_tty"]
        assert result["decision"] == ""
        assert result["flags_restored"]
        assert 0.25 <= result["elapsed"] < 2
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
        os.close(master)
        os.close(slave)


@pytest.fixture
def ceremony(tmp_path, monkeypatch, isolated_runtime):
    clock, _ = isolated_runtime
    monkeypatch.setattr(
        pairing,
        "sys",
        SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True),
            stdout=SimpleNamespace(isatty=lambda: True, encoding="utf-8"),
        ),
    )
    monkeypatch.setattr(pairing.shutil, "get_terminal_size", lambda: os.terminal_size((200, 50)))
    monkeypatch.setattr(pairing, "render_terminal_qr", lambda *args, **kwargs: "synthetic QR")
    monkeypatch.setattr(pairing, "wait_for_listener", lambda directory, config: None)
    grants = Grants(tmp_path)
    other_invite, other_token, other_device = pending_phone(grants, "Unrelated phone")
    _, approved_token, approved_device = pending_phone(grants, "Already approved phone")
    grants.approve(approved_device["id"])
    baseline = copy.deepcopy(state(tmp_path))
    issued = {}
    issue = pairing.invitation_payload

    def claim_own_invitation(directory):
        payload = issue(directory)
        token = secrets.token_urlsafe(32)
        phone = grants.claim(payload["invite"], token, "Own iPhone")
        issued.update(payload=payload, token=token, phone=phone)
        return payload

    monkeypatch.setattr(pairing, "invitation_payload", claim_own_invitation)
    return SimpleNamespace(
        root=tmp_path,
        clock=clock,
        grants=grants,
        other_invite=other_invite,
        other_token=other_token,
        other_device=other_device,
        approved_token=approved_token,
        approved_device=approved_device,
        baseline=baseline,
        issued=issued,
    )


def assert_other_records_unchanged(ceremony):
    after = state(ceremony.root)
    for identifier, device in ceremony.baseline["devices"].items():
        assert after["devices"][identifier] == device
    for identifier, invite in ceremony.baseline["invites"].items():
        assert after["invites"][identifier] == invite
    assert after["host_id"] == ceremony.baseline["host_id"]


@pytest.mark.parametrize("decision", ["y", "yes"])
def test_yes_approves_only_the_phone_from_this_invitation(decision, ceremony, monkeypatch, capsys):
    monkeypatch.setattr(pairing, "read_decision", lambda expires: decision)
    assert pairing.pair(ceremony.root) == 0
    issued = ceremony.issued
    assert_other_records_unchanged(ceremony)
    assert digest(issued["payload"]["invite"]) not in state(ceremony.root)["invites"]
    assert ceremony.grants.authenticate(issued["token"])["id"] == issued["phone"]["id"]
    with pytest.raises(Denied):
        ceremony.grants.authenticate(ceremony.other_token)
    output = capsys.readouterr().out
    assert issued["phone"]["id"] in output
    assert ceremony.other_device["id"] not in output
    assert "Read-only access approved" in output
    for secret in (
        issued["token"],
        ceremony.other_token,
        ceremony.approved_token,
        issued["payload"]["invite"],
    ):
        assert secret not in output
        assert secret not in (ceremony.root / "ducky-grants.json").read_text()


def test_redirected_output_uses_link_fallback_with_interactive_approval(
    ceremony, monkeypatch, capsys
):
    monkeypatch.setattr(pairing.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(pairing, "read_decision", lambda expires: "yes")

    def no_qr_for_redirected_output(link, *, columns, is_tty):
        assert is_tty is False
        return None

    monkeypatch.setattr(pairing, "render_terminal_qr", no_qr_for_redirected_output)
    assert pairing.pair(ceremony.root) == 0
    output = capsys.readouterr().out
    assert "Open this invitation on your iPhone" in output
    assert "ducky://pair?invite=" in output
    assert "Scan with your iPhone camera" not in output


@pytest.mark.parametrize("decision", ["", "n", "no", "cancel"])
def test_decline_removes_only_own_invitation_and_pending_phone(decision, ceremony, monkeypatch):
    monkeypatch.setattr(pairing, "read_decision", lambda expires: decision)
    assert pairing.pair(ceremony.root) == 1
    assert_other_records_unchanged(ceremony)
    assert state(ceremony.root) == ceremony.baseline
    for pending in (False, True):
        with pytest.raises(Denied):
            ceremony.grants.authenticate(ceremony.issued["token"], pending=pending)


def test_keyboard_cancel_removes_only_own_pending_phone(ceremony, monkeypatch):
    def interrupt(expires):
        raise KeyboardInterrupt

    monkeypatch.setattr(pairing, "read_decision", interrupt)
    assert pairing.pair(ceremony.root) == 130
    assert state(ceremony.root) == ceremony.baseline
    with pytest.raises(Denied):
        ceremony.grants.authenticate(ceremony.issued["token"], pending=True)


def test_expiry_while_deciding_cannot_grant_status_access(ceremony, monkeypatch):
    def too_late(expires):
        ceremony.clock.now = expires
        return "yes"

    monkeypatch.setattr(pairing, "read_decision", too_late)
    with pytest.raises(Denied, match="No unexpired pending device"):
        pairing.pair(ceremony.root)
    assert state(ceremony.root) == ceremony.baseline
    with pytest.raises(Denied):
        ceremony.grants.authenticate(ceremony.issued["token"], pending=True)


def test_other_pending_phone_is_never_offered_while_waiting_for_own_invitation(
    ceremony, monkeypatch, capsys
):
    issue = invitation_payload

    def unclaimed(directory):
        payload = issue(directory)
        ceremony.issued["payload"] = payload
        return payload

    def no_prompt(expires):
        raise AssertionError("An unrelated pending phone must never reach owner approval")

    monkeypatch.setattr(pairing, "invitation_payload", unclaimed)
    monkeypatch.setattr(pairing, "read_decision", no_prompt)
    monkeypatch.setattr(pairing.time, "sleep", lambda seconds: ceremony.clock.sleep(601))
    assert pairing.pair(ceremony.root) == 1
    assert state(ceremony.root) == ceremony.baseline
    output = capsys.readouterr().out
    assert "Invitation expired" in output
    assert ceremony.other_device["id"] not in output


def test_one_interactive_ceremony_per_scratch_home(tmp_path):
    with (
        pairing.pairing_lock(tmp_path),
        pytest.raises(ValueError, match="Another Ducky pairing"),
        pairing.pairing_lock(tmp_path),
    ):
        pytest.fail("Concurrent ceremony acquired the same lock")
    with pairing.pairing_lock(tmp_path):
        pass
    assert (tmp_path / ".ducky-pair.lock").stat().st_mode & 0o777 == 0o600


def test_grant_correlation_cannot_approve_another_invitations_device(tmp_path):
    grants = Grants(tmp_path)
    first, first_token, first_device = pending_phone(grants, "First")
    second, second_token, second_device = pending_phone(grants, "Second")
    assert grants.pending_invitation(first["invite"])["id"] == first_device["id"]
    before = (tmp_path / "ducky-grants.json").read_bytes()
    with pytest.raises(Denied):
        grants.approve_invitation(first["invite"], second_device["id"])
    assert (tmp_path / "ducky-grants.json").read_bytes() == before
    grants.approve_invitation(first["invite"], first_device["id"])
    grants.cancel_invitation(first["invite"])
    assert grants.authenticate(first_token)["status"] == "active"
    with pytest.raises(Denied):
        grants.authenticate(second_token)
    assert grants.pending_invitation(second["invite"])["id"] == second_device["id"]


def test_last_seen_tracks_successful_authentication_and_throttles_writes(tmp_path, monkeypatch):
    grants = Grants(tmp_path)
    _, token, device = pending_phone(grants)
    assert grants.devices()[0]["last_seen_at"] is None
    grants.approve(device["id"])
    writes = []
    write = grants_module.private_write

    def record_write(path, text):
        writes.append(path)
        return write(path, text)

    monkeypatch.setattr(grants_module, "private_write", record_write)
    assert grants.authenticate(token, now=10_000)["id"] == device["id"]
    assert grants.devices()[0]["last_seen_at"] == 10_000
    first_bytes = grants.path.read_bytes()
    for now in (10_001, 10_059):
        grants.authenticate(token, now=now)
        assert grants.path.read_bytes() == first_bytes
    assert writes == [grants.path]
    grants.authenticate(token, now=10_060)
    assert grants.devices()[0]["last_seen_at"] == 10_060
    assert writes == [grants.path, grants.path]
    assert token not in grants.path.read_text()
    assert token not in json.dumps(grants.devices())
    assert "token_hash" not in json.dumps(grants.devices())
    assert "token_hash" not in json.dumps(grants.authenticate(token, now=10_061))


def test_failed_authentication_does_not_update_last_seen_or_write(tmp_path):
    grants = Grants(tmp_path)
    _, token, _ = pending_phone(grants)
    original = grants.path.read_bytes()
    for credential, now in ((token, 10_000), (secrets.token_urlsafe(32), 10_000), ("bad", 10_000)):
        with pytest.raises(Denied):
            grants.authenticate(credential, now=now)
        assert grants.path.read_bytes() == original
    with pytest.raises(Denied):
        grants.authenticate(token, pending=True, now=10_600)
    assert grants.path.read_bytes() == original
    assert grants.devices()[0]["last_seen_at"] is None


def test_legacy_device_without_last_seen_remains_usable(tmp_path):
    grants = Grants(tmp_path)
    _, token, device = pending_phone(grants)
    grants.approve(device["id"])
    legacy = state(tmp_path)
    legacy["devices"][device["id"]].pop("last_seen_at")
    private_write(grants.path, json.dumps(legacy))
    assert grants.devices()[0]["last_seen_at"] is None
    assert grants.authenticate(token, now=10_050)["status"] == "active"
    assert grants.devices()[0]["last_seen_at"] == 10_050


def test_pending_authentication_records_contact_without_granting_status_access(tmp_path):
    grants = Grants(tmp_path)
    _, token, _ = pending_phone(grants)
    assert grants.authenticate(token, pending=True, now=10_010)["status"] == "pending"
    assert grants.devices()[0]["last_seen_at"] == 10_010
    with pytest.raises(Denied):
        grants.authenticate(token, now=10_011)
    assert grants.devices()[0]["last_seen_at"] == 10_010
