"""Private JSONL transport: real pipes and grants, no apps or network discovery."""

import argparse
import copy
import fcntl
import json
import os
import secrets
import signal
import time
from types import SimpleNamespace

import pytest

from duckterm.mobile import cli, pairing
from duckterm.mobile import pairing_stream as stream
from duckterm.mobile.grants import Denied, Grants, digest


@pytest.fixture
def pipe(monkeypatch):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r")
    state = SimpleNamespace(source=source, writer=write_fd)

    def send(raw):
        os.write(state.writer, raw)

    def close():
        if state.writer is not None:
            os.close(state.writer)
            state.writer = None

    state.send = send
    state.close = close
    monkeypatch.setattr(stream, "sys", SimpleNamespace(stdin=source))
    yield state
    close()
    source.close()


def grant_state(root):
    return json.loads((root / "ducky-grants.json").read_text())


@pytest.fixture
def ceremony(tmp_path, monkeypatch, pipe):
    grants = Grants(tmp_path)
    unrelated_invite = grants.invite()
    unrelated_token = secrets.token_urlsafe(32)
    unrelated = grants.claim(unrelated_invite["invite"], unrelated_token, "Unrelated phone")
    active_invite = grants.invite()
    active_token = secrets.token_urlsafe(32)
    active = grants.claim(active_invite["invite"], active_token, "Existing approved phone")
    grants.approve(active["id"])
    fixture = SimpleNamespace(
        root=tmp_path,
        grants=grants,
        baseline=copy.deepcopy(grant_state(tmp_path)),
        unrelated=unrelated,
        active=active,
        active_token=active_token,
        unrelated_token=unrelated_token,
        issued=None,
        token=secrets.token_urlsafe(32),
        phone=None,
        events=[],
        on_emit=lambda state, fields: None,
        pipe=pipe,
    )

    def issue(root):
        assert root == fixture.root
        fixture.issued = {
            "version": 1,
            **grants.invite(),
            "url": "https://127.0.0.1:44331",
            "certificate_sha256": "a" * 64,
        }
        return fixture.issued

    real_emit = stream.emit

    def emit(state, **fields):
        fixture.events.append({"version": 1, "state": state, **fields})
        real_emit(state, **fields)
        fixture.on_emit(state, fields)

    def claim():
        fixture.phone = grants.claim(fixture.issued["invite"], fixture.token, "Own fixture phone")

    def command(action, identifier=None):
        record = {"action": action}
        if identifier is not None:
            record["device_id"] = identifier
        pipe.send(json.dumps(record).encode() + b"\n")

    fixture.claim = claim
    fixture.command = command
    monkeypatch.setattr(stream, "prepare", lambda *args, **kwargs: {"port": 44331})
    monkeypatch.setattr(stream, "wait_for_listener", lambda *args: None)
    monkeypatch.setattr(stream, "invitation_payload", issue)
    monkeypatch.setattr(stream, "emit", emit)
    return fixture


def assert_others_unchanged(fixture):
    current = grant_state(fixture.root)
    for section in ("invites", "devices"):
        for identifier, value in fixture.baseline[section].items():
            assert current[section][identifier] == value
    assert current["host_id"] == fixture.baseline["host_id"]


def assert_own_invite_removed(fixture):
    assert digest(fixture.issued["invite"]) not in grant_state(fixture.root)["invites"]


def test_partial_pipe_input_respects_poll_deadline_and_restores_fd_flags(pipe):
    original = fcntl.fcntl(pipe.source, fcntl.F_GETFL)
    with stream.commands() as incoming:
        assert fcntl.fcntl(pipe.source, fcntl.F_GETFL) & os.O_NONBLOCK
        pipe.send(b'{"action":"can')
        assert incoming.poll(0.05) is None
        started = time.monotonic()
        assert incoming.poll(0.05) is None
        assert 0.025 <= time.monotonic() - started < 1.5
        pipe.send(b'cel"}\n')
        assert incoming.poll(0) == {"action": "cancel"}
    assert fcntl.fcntl(pipe.source, fcntl.F_GETFL) == original


def test_multiple_pipe_commands_are_framed_without_waiting_for_more_input(pipe):
    with stream.commands() as incoming:
        pipe.send(b'{"action":"deny","device_id":"first"}\n{"action":"cancel"}\n')
        assert incoming.poll(0) == {"action": "deny", "device_id": "first"}
        assert incoming.poll(0) == {"action": "cancel"}


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json\n",
        b"[]\n",
        b"null\n",
        b'{"action":"cancel","extra":"secret"}\n',
        b'{"action":"approve"}\n',
        b'{"action":"approve","device_id":1}\n',
        b'{"action":"allow","device_id":"phone"}\n',
        b'"\xff"\n',
        b"x" * 4096,
    ],
)
def test_invalid_or_oversized_commands_have_fixed_error_and_restore_flags(pipe, raw):
    original = fcntl.fcntl(pipe.source, fcntl.F_GETFL)
    with (
        pytest.raises(stream.CeremonyError, match="^invalid_command$"),
        stream.commands() as incoming,
    ):
        pipe.send(raw)
        incoming.poll(0)
    assert fcntl.fcntl(pipe.source, fcntl.F_GETFL) == original


@pytest.mark.parametrize("action", ["approve", "deny", "cancel", "eof"])
def test_only_this_invitation_can_be_decided_and_existing_grants_survive(ceremony, capsys, action):
    def react(state, fields):
        if state == "invitation":
            ceremony.claim()
        elif state == "pending":
            assert fields["device"]["id"] == ceremony.phone["id"]
            if action == "eof":
                ceremony.pipe.send(b'{"action":"approve"')
                ceremony.pipe.close()
            else:
                ceremony.command(action, ceremony.phone["id"] if action != "cancel" else None)

    ceremony.on_emit = react
    assert stream.pair_json(ceremony.root) == 0
    assert [event["state"] for event in ceremony.events] == [
        "invitation",
        "pending",
        {"approve": "approved", "deny": "denied", "cancel": "cancelled", "eof": "cancelled"}[
            action
        ],
    ]
    assert_others_unchanged(ceremony)
    assert_own_invite_removed(ceremony)
    if action == "approve":
        assert ceremony.grants.authenticate(ceremony.token)["id"] == ceremony.phone["id"]
    else:
        with pytest.raises(Denied):
            ceremony.grants.authenticate(ceremony.token, pending=True)
    output = capsys.readouterr().out
    assert [json.loads(line) for line in output.splitlines()] == ceremony.events
    for secret in (ceremony.token, ceremony.active_token, ceremony.unrelated_token):
        assert secret not in output


def test_unrelated_pending_phone_is_never_offered(ceremony):
    def react(state, fields):
        if state == "invitation":
            ceremony.pipe.close()

    ceremony.on_emit = react
    assert stream.pair_json(ceremony.root) == 0
    assert [event["state"] for event in ceremony.events] == ["invitation", "cancelled"]
    assert grant_state(ceremony.root) == ceremony.baseline


@pytest.mark.parametrize(
    "raw",
    [b"not-json\n", b"x" * 4096, b"[" * 1200 + b"0" + b"]" * 1200 + b"\n"],
)
def test_invalid_pending_input_cleans_invitation_and_phone(ceremony, raw):
    def react(state, fields):
        if state == "invitation":
            ceremony.claim()
        elif state == "pending":
            ceremony.pipe.send(raw)

    ceremony.on_emit = react
    assert stream.pair_json(ceremony.root) == 1
    assert ceremony.events[-1] == {"version": 1, "state": "error", "code": "invalid_command"}
    assert grant_state(ceremony.root) == ceremony.baseline


@pytest.mark.parametrize(
    "bad_field", ["host_id", "invites", "devices", "invite_entry", "device_entry"]
)
def test_malformed_grant_file_reports_fixed_error_without_trace(ceremony, capsys, bad_field):
    malformed = copy.deepcopy(ceremony.baseline)
    if bad_field == "host_id":
        del malformed[bad_field]
    elif bad_field in ("invite_entry", "device_entry"):
        section = "invites" if bad_field == "invite_entry" else "devices"
        malformed[section][next(iter(malformed[section]))] = []
    else:
        malformed[bad_field] = []
    ceremony.grants.path.write_text(json.dumps(malformed))
    original = ceremony.grants.path.read_bytes()
    assert stream.pair_json(ceremony.root) == 1
    assert ceremony.events[-1] == {"version": 1, "state": "error", "code": "pairing_failed"}
    assert ceremony.issued is None
    assert ceremony.grants.path.read_bytes() == original
    assert "Traceback" not in capsys.readouterr().err


def test_approval_for_wrong_device_is_rejected_and_own_pending_phone_removed(ceremony):
    def react(state, fields):
        if state == "invitation":
            ceremony.claim()
        elif state == "pending":
            ceremony.command("approve", ceremony.unrelated["id"])

    ceremony.on_emit = react
    assert stream.pair_json(ceremony.root) == 1
    assert ceremony.events[-1] == {"version": 1, "state": "error", "code": "invalid_command"}
    assert grant_state(ceremony.root) == ceremony.baseline


@pytest.mark.parametrize("change", ["expired", "revoked", "already_approved"])
def test_approval_rechecks_expiry_and_grant_after_input(ceremony, monkeypatch, change):
    now = time.time()
    clock = [now]
    monkeypatch.setattr(stream.time, "time", lambda: clock[0])

    def react(state, fields):
        if state == "invitation":
            ceremony.claim()
        elif state == "pending":
            if change == "expired":
                clock[0] = fields["device"]["expires_at"]
            elif change == "revoked":
                ceremony.grants.revoke(ceremony.phone["id"])
            else:
                ceremony.grants.approve(ceremony.phone["id"])
            ceremony.command("approve", ceremony.phone["id"])

    ceremony.on_emit = react
    assert stream.pair_json(ceremony.root) == 1
    assert_others_unchanged(ceremony)
    assert_own_invite_removed(ceremony)
    if change == "expired":
        assert ceremony.events[-1] == {"version": 1, "state": "expired", "phase": "pending"}
    else:
        assert ceremony.events[-1] == {
            "version": 1,
            "state": "error",
            "code": "approval_unavailable",
        }
    if change == "already_approved":
        assert ceremony.grants.authenticate(ceremony.token)["status"] == "active"
    else:
        with pytest.raises(Denied):
            ceremony.grants.authenticate(ceremony.token, pending=True)


def test_partial_command_cannot_extend_ceremony_past_monotonic_deadline(ceremony, monkeypatch):
    clock = [1000.0]
    monotonic = [200.0]
    monkeypatch.setattr(stream.time, "time", lambda: clock[0])
    monkeypatch.setattr(stream.time, "monotonic", lambda: monotonic[0])
    real_poll = stream.Commands.poll

    def advance_after_partial(self, timeout):
        result = real_poll(self, 0)
        if self.buffer:
            # A wall-clock correction must not lengthen the original ten minutes.
            clock[0] -= 3600
            monotonic[0] += 601
        return result

    def react(state, fields):
        if state == "invitation":
            ceremony.pipe.send(b'{"action":"approve"')

    ceremony.on_emit = react
    monkeypatch.setattr(stream.Commands, "poll", advance_after_partial)
    assert stream.pair_json(ceremony.root) == 1
    assert ceremony.events[-1] == {"version": 1, "state": "expired", "phase": "invitation"}
    assert grant_state(ceremony.root) == ceremony.baseline


@pytest.mark.parametrize("cancel", ["command", "eof"])
def test_cancel_during_setup_never_issues_invitation(ceremony, monkeypatch, cancel):
    def finish_setup(*args):
        if cancel == "command":
            ceremony.command("cancel")
        else:
            ceremony.pipe.close()

    monkeypatch.setattr(stream, "wait_for_listener", finish_setup)
    assert stream.pair_json(ceremony.root) == 0
    assert ceremony.issued is None
    assert ceremony.events == [{"version": 1, "state": "cancelled"}]
    assert grant_state(ceremony.root) == ceremony.baseline


@pytest.mark.parametrize(
    ("stage", "code"),
    [("prepare", "setup_unavailable"), ("wait_for_listener", "listener_unavailable")],
)
def test_setup_errors_are_fixed_codes_without_exception_secrets(
    ceremony, monkeypatch, capsys, stage, code
):
    secret = "synthetic-private-token-and-user-path"

    def failure(*args, **kwargs):
        raise OSError(secret)

    monkeypatch.setattr(stream, stage, failure)
    assert stream.pair_json(ceremony.root) == 1
    assert ceremony.events == [{"version": 1, "state": "error", "code": code}]
    assert ceremony.issued is None
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert grant_state(ceremony.root) == ceremony.baseline


def test_lock_contention_reports_busy_without_touching_grants(ceremony):
    with pairing.pairing_lock(ceremony.root):
        assert stream.pair_json(ceremony.root) == 1
    assert ceremony.events == [{"version": 1, "state": "error", "code": "pairing_busy"}]
    assert ceremony.issued is None
    assert grant_state(ceremony.root) == ceremony.baseline


def test_broken_native_output_cleans_own_pending_phone(ceremony, monkeypatch):
    original_emit = stream.emit

    def closed_output(state, **fields):
        if state == "pending":
            raise BrokenPipeError("synthetic broken native pipe")
        original_emit(state, **fields)

    ceremony.on_emit = lambda state, fields: ceremony.claim() if state == "invitation" else None
    monkeypatch.setattr(stream, "emit", closed_output)
    assert stream.pair_json(ceremony.root) == 1
    assert grant_state(ceremony.root) == ceremony.baseline


def test_native_termination_handler_cleans_pending_phone_and_restores_previous_handler(ceremony):
    original_handler = signal.getsignal(signal.SIGTERM)
    original_flags = fcntl.fcntl(ceremony.pipe.source, fcntl.F_GETFL)

    def react(state, fields):
        if state == "invitation":
            ceremony.claim()
        elif state == "pending":
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler)
            assert handler is not original_handler
            # Invoke only this callback. No signal is sent to pytest or any process.
            handler(signal.SIGTERM, None)

    ceremony.on_emit = react
    assert stream.pair_json(ceremony.root) == 130
    assert ceremony.events[-1] == {"version": 1, "state": "cancelled"}
    assert grant_state(ceremony.root) == ceremony.baseline
    assert signal.getsignal(signal.SIGTERM) is original_handler
    assert fcntl.fcntl(ceremony.pipe.source, fcntl.F_GETFL) == original_flags
    with pairing.pairing_lock(ceremony.root):
        pass  # Termination also releases the ceremony lock for the next window.


@pytest.mark.parametrize("json_mode", [True, False])
def test_cli_parser_dispatch_keeps_json_and_interactive_transports_separate(
    tmp_path, monkeypatch, json_mode
):
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="command", required=True))
    arguments = ["ducky", "pair", "--address", "127.0.0.1", "--port", "44332"]
    if json_mode:
        arguments.append("--json")
    calls = []

    def json_pair(root, **kwargs):
        calls.append(("json", root, kwargs))
        return 23

    def interactive_pair(root, **kwargs):
        calls.append(("interactive", root, kwargs))
        return 24

    monkeypatch.setattr(cli.instance, "home", lambda: tmp_path)
    monkeypatch.setattr(stream, "pair_json", json_pair)
    monkeypatch.setattr(pairing, "pair", interactive_pair)
    assert cli.run(parser.parse_args(arguments)) == (23 if json_mode else 24)
    assert calls == [
        (
            "json" if json_mode else "interactive",
            tmp_path,
            {"selected_address": "127.0.0.1", "port": 44332},
        )
    ]
