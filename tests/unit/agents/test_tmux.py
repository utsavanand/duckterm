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
