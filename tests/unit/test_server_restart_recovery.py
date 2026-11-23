"""Force-exit recovery must retain workspace and process identity checks."""

import os
import signal
import time

import pytest

from duckterm import cli


def test_process_birth_is_available_for_this_process():
    birth = cli._process_birth(os.getpid())
    assert birth is not None
    assert cli._process_birth(os.getpid()) == birth
    assert cli._process_birth(2_000_000_000) is None


@pytest.mark.parametrize("changed", ["birth", "pidfile", "listener", "unknown_birth", None])
def test_restart_only_forces_verified_original_after_listener_closed(monkeypatch, changed):
    pidfile = cli.instance.home() / "server.pid"
    pidfile.write_text("42")
    monkeypatch.setattr(cli, "_duckterm_responds", lambda *a: True)
    monkeypatch.setattr(cli, "_restart_identity_matches", lambda *a: True)
    reads = 0
    killed = False
    signals = []
    served = []

    def birth(pid):
        nonlocal reads
        reads += 1
        if changed == "unknown_birth":
            return None
        return "new" if changed == "birth" and reads > 1 else "original"

    listener_reads = 0

    def listeners(port):
        nonlocal listener_reads
        listener_reads += 1
        if listener_reads == 1:
            return {42}
        if changed == "pidfile":
            pidfile.write_text("99")
        return {99} if changed == "listener" else set()

    def kill(pid, sig):
        nonlocal killed
        assert pid == 42
        signals.append(sig)
        if sig == signal.SIGKILL:
            killed = True

    monkeypatch.setattr(cli, "_process_birth", birth)
    monkeypatch.setattr(cli, "_listening_pids", listeners)
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: not killed)
    monkeypatch.setattr(cli.os, "kill", kill)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    monkeypatch.setattr(cli, "_serve", lambda *a, **kw: served.append(kw) or 0)
    assert cli._restart("127.0.0.1", 4300) == (0 if changed is None else 1)
    assert signals == ([signal.SIGTERM, signal.SIGKILL] if changed is None else [signal.SIGTERM])
    assert bool(served) == (changed is None)
