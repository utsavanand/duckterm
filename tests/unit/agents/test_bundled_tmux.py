"""Resolver failures must never turn an inaccessible server into empty state."""

import subprocess

import pytest

from duckterm.agents import tmux


@pytest.fixture(autouse=True)
def bundle(monkeypatch):
    tmux._bundled_selection.cache_clear()
    monkeypatch.setenv("DUCKTERM_BUNDLED_TMUX", "/app with spaces/tmux")
    monkeypatch.setenv("DUCKTERM_TMUX_SOCKET", "test-bundle-private")
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/system/tmux")
    yield
    tmux._bundled_selection.cache_clear()


@pytest.mark.parametrize(
    "error",
    [
        "",
        "no server running on /private/socket",
        "error connecting to /private/socket (No such file or directory)",
    ],
)
def test_bundle_first_same_socket_cached(monkeypatch, error):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, int(bool(error)) if "-L" in args else 0, "", error)

    monkeypatch.setattr(tmux.subprocess, "run", run)
    assert tmux.has_tmux()
    assert tmux.selected_client() == ("/app with spaces/tmux", "bundled")
    assert calls == [
        ["/app with spaces/tmux", "-V"],
        ["/app with spaces/tmux", "-L", "test-bundle-private", "list-sessions"],
    ]


@pytest.mark.parametrize("failure", ["exec", "protocol"])
def test_system_fallback_keeps_socket(monkeypatch, failure):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0].startswith("/app"):
            if failure == "exec":
                raise OSError("bad architecture")
            if "-L" in args:
                return subprocess.CompletedProcess(args, 1, "", "protocol version mismatch")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", run)
    assert tmux.selected_client() == ("/system/tmux", "system")
    assert ["/system/tmux", "-L", "test-bundle-private", "list-sessions"] in calls


def test_unknown_socket_failure_is_not_missing_tmux(monkeypatch):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, int("-L" in args), "", "Permission denied")

    monkeypatch.setattr(tmux.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Permission denied"):
        tmux.has_tmux()


def test_missing_not_cached_recheck_can_recover(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("missing")

    monkeypatch.setattr(tmux.subprocess, "run", broken)
    assert not tmux.has_tmux()
    monkeypatch.setattr(
        tmux.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "", "")
    )
    assert tmux.has_tmux()


def test_socket_timeout_is_not_missing_tmux(monkeypatch):
    def run(args, **kwargs):
        if "-L" in args:
            raise subprocess.TimeoutExpired(args, 3)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="cannot probe existing tmux server"):
        tmux.has_tmux()


def test_commands_use_selected_client(monkeypatch):
    monkeypatch.setattr(tmux, "selected_client", lambda: ("/bundle/tmux", "bundled"))
    assert tmux.client_command("list-sessions") == [
        "/bundle/tmux",
        "-L",
        "test-bundle-private",
        "list-sessions",
    ]


@pytest.mark.parametrize("source", ["bundled", "system"])
def test_commands_and_terminal_attach_share_selected_client(monkeypatch, source):
    binary = "/app with spaces/tmux" if source == "bundled" else "/system/tmux"
    monkeypatch.setattr(tmux, "selected_client", lambda: (binary, source))
    attach = tmux.client_command("-C", "attach-session", "-t", "rd_test")
    assert attach == [binary, "-L", "test-bundle-private", "-C", "attach-session", "-t", "rd_test"]
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", run)
    tmux.capture_pane("rd_test")
    assert calls[0][:3] == attach[:3]
