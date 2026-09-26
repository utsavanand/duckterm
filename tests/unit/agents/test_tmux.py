import shutil
import time

import pytest

from duckterm.agents import tmux

_HAS_TMUX = shutil.which("tmux") is not None


def test_target_naming_is_prefixed() -> None:
    assert tmux.target_for("abc123") == "rd_abc123"


def test_has_tmux_matches_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    assert tmux.has_tmux() is True
    monkeypatch.setattr(tmux.shutil, "which", lambda _: None)
    assert tmux.has_tmux() is False


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_capture_screen_trims_pane_padding_and_uses_crlf() -> None:
    """The pane is 120x40 but the browser viewport is usually shorter — the
    attach snapshot must not include the pane's trailing blank rows (they
    scroll short output out of view) and must join lines with CRLF (bare LF
    doesn't return to column 0 in a raw terminal)."""
    target = tmux.spawn("test-cap", "echo BANNER_LINE; sleep 5", cwd="/tmp")
    try:
        deadline = time.time() + 5
        screen = b""
        while time.time() < deadline and b"BANNER_LINE" not in screen:
            screen = tmux.capture_screen(target)
            time.sleep(0.1)
        assert screen.endswith(b"BANNER_LINE")  # trailing blank rows dropped
        assert b"\n" not in screen.replace(b"\r\n", b"")  # no bare LFs
    finally:
        tmux.kill_session(target)


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_capture_screen_includes_scrolled_off_history() -> None:
    """Output that scrolled past the pane top must still reach an attaching
    terminal — it lives in tmux history, and the snapshot pulls it in (-S).
    Without it, the browser's scrollback was empty above the visible screen."""
    target = tmux.spawn("test-hist", "seq 1 200; sleep 5", cwd="/tmp")
    try:
        deadline = time.time() + 5
        screen = b""
        while time.time() < deadline and b"200" not in screen:
            screen = tmux.capture_screen(target)
            time.sleep(0.1)
        # The pane is 40 rows: line 1 scrolled off long ago, yet the snapshot
        # has it (history included) — and still ends at the live screen.
        assert b"\r\n1\r\n" in b"\r\n" + screen
        assert screen.endswith(b"200") or b"200" in screen
        # Visible-screen-only capture (history_lines=0) must NOT have line 1.
        visible = tmux.capture_screen(target, history_lines=0)
        assert b"\r\n1\r\n" not in b"\r\n" + visible
    finally:
        tmux.kill_session(target)


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_spawn_capture_kill_roundtrip() -> None:
    # A real end-to-end on the dedicated socket so we never touch the user's tmux.
    target = tmux.spawn("test-rt", "echo hello-from-tmux; sleep 5", cwd="/tmp")
    try:
        assert tmux.session_exists(target)
        # capture may be empty until the command prints; just assert it returns a str.
        assert isinstance(tmux.capture_pane(target), str)
    finally:
        assert tmux.kill_session(target)
        assert not tmux.session_exists(target)


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_private_server_survives_last_agent_exit(monkeypatch, tmp_path) -> None:
    # Use our own socket so another live fixture cannot hide exit-empty races.
    monkeypatch.setenv("DUCKTERM_TMUX_SOCKET", f"fork-exit-{tmp_path.name}")
    try:
        target = tmux.spawn("quick", "true", cwd=str(tmp_path))
        deadline = time.monotonic() + 5
        while tmux.session_exists(target) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not tmux.session_exists(target)
        ok, before = tmux._tmux("display-message", "-p", "#{pid}")
        assert ok and before.strip().isdigit()  # Server survived its last child.
        second = tmux.spawn("next", "sleep 5", cwd=str(tmp_path))
        assert tmux.session_exists(second)
        ok, after = tmux._tmux("display-message", "-p", "#{pid}")
        assert ok and after == before  # No shutdown/restart gap between agents.
    finally:
        tmux._tmux("kill-server")


@pytest.mark.parametrize(
    "error",
    [
        "no server running on /tmp/test/socket",
        "error connecting to /tmp/test/socket (No such file or directory)",
    ],
)
def test_discovery_absent_server_is_empty(monkeypatch, error):
    monkeypatch.setattr(tmux, "_tmux", lambda *args: (False, error))
    assert tmux.list_duckterm_sessions() == []


def test_discovery_failure_is_not_an_empty_fleet(monkeypatch):
    monkeypatch.setattr(
        tmux,
        "_tmux",
        lambda *args: (False, "error connecting to /tmp/test/socket (Permission denied)"),
    )
    with pytest.raises(RuntimeError, match="cannot discover"):
        tmux.list_duckterm_sessions()


def test_discovery_excludes_dead_panes_and_foreign_sessions(monkeypatch):
    monkeypatch.setattr(
        tmux, "_tmux", lambda *args: (True, "rd_alive\t0\nrd_dead\t1\nother\t0\nrd_alive\t0\n")
    )
    assert tmux.list_duckterm_sessions() == ["alive"]
