"""Resolver failures must never turn an inaccessible server into empty state."""

import asyncio
import subprocess
import threading

import pytest

from duckterm.agents import tmux, tmux_stream


@pytest.fixture(autouse=True)
def bundle(monkeypatch):
    tmux._bundled_selection.cache_clear()
    monkeypatch.setenv("DUCKTERM_BUNDLED_TMUX", "/app with spaces/tmux")
    monkeypatch.setenv("DUCKTERM_TMUX_SOCKET", "test-bundle-private")
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/system/tmux")
    yield
    tmux._bundled_selection.cache_clear()


def fake_run(monkeypatch, respond):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return respond(args)

    monkeypatch.setattr(tmux.subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    "error",
    [
        "",
        "no server running on /private/socket",
        "error connecting to /private/socket (No such file or directory)",
    ],
)
def test_bundle_first_on_same_socket_and_cached(monkeypatch, error):
    def respond(args):
        return subprocess.CompletedProcess(args, int(bool(error) and "-L" in args), "", error)

    calls = fake_run(monkeypatch, respond)
    assert tmux.has_tmux()
    assert tmux.selected_client() == ("/app with spaces/tmux", "bundled")
    assert calls == [
        ["/app with spaces/tmux", "-V"],
        ["/app with spaces/tmux", "-L", "test-bundle-private", "list-sessions"],
    ]


@pytest.mark.parametrize("failure", ["exec", "protocol"])
def test_system_fallback_keeps_socket(monkeypatch, failure):
    def respond(args):
        if args[0].startswith("/app"):
            if failure == "exec":
                raise OSError("bad architecture")
            if "-L" in args:
                return subprocess.CompletedProcess(args, 1, "", "protocol version mismatch")
        return subprocess.CompletedProcess(args, 0, "", "")

    calls = fake_run(monkeypatch, respond)
    assert tmux.selected_client() == ("/system/tmux", "system")
    assert ["/system/tmux", "-L", "test-bundle-private", "list-sessions"] in calls


def test_relative_bundle_path_is_never_executed(monkeypatch):
    monkeypatch.setenv("DUCKTERM_BUNDLED_TMUX", "tmux")
    calls = fake_run(monkeypatch, lambda args: subprocess.CompletedProcess(args, 0, "", ""))
    assert tmux.selected_client() == ("/system/tmux", "system")
    assert all(call[0] == "/system/tmux" for call in calls)


def test_unset_bundle_keeps_path_tmux(monkeypatch):
    monkeypatch.delenv("DUCKTERM_BUNDLED_TMUX")
    calls = fake_run(monkeypatch, lambda args: subprocess.CompletedProcess(args, 0, "", ""))
    assert tmux.has_tmux()
    assert tmux.client_command("list-sessions") == [
        "tmux",
        "-L",
        "test-bundle-private",
        "list-sessions",
    ]
    assert calls == []


@pytest.mark.parametrize("failure", ["socket-error", "probe-timeout"])
def test_unknown_server_state_is_not_empty_or_missing(monkeypatch, failure):
    def respond(args):
        if "-L" not in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if failure == "probe-timeout":
            raise subprocess.TimeoutExpired(args, 3)
        return subprocess.CompletedProcess(args, 1, "", "Permission denied")

    fake_run(monkeypatch, respond)
    assert tmux.has_tmux()
    with pytest.raises(RuntimeError, match="cannot discover tmux sessions"):
        tmux.list_duckterm_sessions()
    with pytest.raises(RuntimeError, match="cannot discover tmux sessions"):
        tmux.session_targets()
    with pytest.raises(ValueError, match="cannot probe existing tmux server"):
        tmux.spawn("fixture", "/bin/cat", "/")


def test_missing_is_not_cached_and_recheck_recovers(monkeypatch):
    def missing(args):
        raise OSError("missing")

    fake_run(monkeypatch, missing)
    assert not tmux.has_tmux()
    fake_run(monkeypatch, lambda args: subprocess.CompletedProcess(args, 0, "", ""))
    assert tmux.has_tmux()


@pytest.mark.parametrize("source", ["bundled", "system"])
def test_commands_and_terminal_attach_share_selected_client(monkeypatch, source):
    binary = "/app with spaces/tmux" if source == "bundled" else "/system/tmux"
    monkeypatch.setattr(tmux, "selected_client", lambda: (binary, source))
    calls = fake_run(monkeypatch, lambda args: subprocess.CompletedProcess(args, 0, "", ""))
    tmux.capture_pane("rd_test")
    assert calls[0][:3] == [binary, "-L", "test-bundle-private"]
    assert tmux.client_command("-C", "attach-session")[:3] == calls[0][:3]


def test_stream_selects_off_loop_and_closes_only_the_viewer(monkeypatch):
    loop_thread = threading.get_ident()
    threads = []

    def unavailable(*args):
        threads.append(threading.get_ident())
        raise RuntimeError("cannot probe existing tmux server: Permission denied")

    monkeypatch.setattr(tmux_stream.tmux, "client_command", unavailable)

    async def scenario():
        with pytest.raises(StopAsyncIteration):
            await anext(tmux_stream.stream("rd_fixture"))

    asyncio.run(scenario())
    assert threads and threads[0] != loop_thread
