"""Thin wrapper over tmux for persistent, controllable agent sessions.

A tmux-backed session survives the Duckterm server restarting and gives a
clean way to inject keystrokes (used by the approval workflow). We isolate our
sessions on a dedicated tmux socket so they never collide with the user's own
tmux server.

Ported from uv-suite's watchtower tmux service. All calls are synchronous
subprocess; drive them from async code via asyncio.to_thread.
"""

import os
import shutil
import subprocess

_PREFIX = "rd_"


def socket_name() -> str:
    """The tmux socket namespace. Tests and the e2e harness set
    DUCKTERM_TMUX_SOCKET to their own value so their panes never mix with the
    user's real sessions — and can be swept wholesale (kill-server) afterwards.
    Before this, e2e runs leaked their cat/fixture panes onto the user's
    socket; ~100 leftovers made every tmux call (send-keys, has-session)
    slow enough to flake the terminal specs."""
    return os.environ.get("DUCKTERM_TMUX_SOCKET", "duckterm")


def has_tmux() -> bool:
    return shutil.which("tmux") is not None


def _tmux(*args: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["tmux", "-L", socket_name(), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, (result.stdout if result.returncode == 0 else result.stderr)


def target_for(session_id: str) -> str:
    return f"{_PREFIX}{session_id}"


def spawn(session_id: str, command: str, cwd: str) -> str:
    """Create a detached tmux session running `command` in `cwd`. Returns the
    tmux target name."""
    target = target_for(session_id)
    # `-x/-y` set the initial size; a detached session otherwise defaults to
    # 80x24, which mismatches the browser pane and garbles a TUI's wrapping.
    _tmux("new-session", "-d", "-s", target, "-x", "120", "-y", "40", "-c", cwd, command)
    # window-size manual: without it tmux sizes the window to the LARGEST/LATEST
    # attached client (none, for a detached session), so resize-window from the
    # browser is ignored. Manual makes our resize authoritative.
    _tmux("set-option", "-t", target, "window-size", "manual")
    return target


def spawn_piped(session_id: str, command: str, cwd: str, pipe_path: str) -> str:
    """Spawn a detached session and stream its pane output to `pipe_path` from
    the start, so live output isn't missed. Returns the tmux target."""
    target = spawn(session_id, command, cwd)
    # -o starts piping immediately; appends raw pane output to the file.
    _tmux("pipe-pane", "-t", target, "-o", f"cat >> {pipe_path}")
    return target


def list_duckterm_sessions() -> list[str]:
    """All live session ids Duckterm spawned (the rd_<id> targets, id only)."""
    ok, out = _tmux("list-sessions", "-F", "#{session_name}")
    if not ok:
        return []
    return [name[len(_PREFIX) :] for name in out.split() if name.startswith(_PREFIX)]


def send_keys(target: str, keys: str, *, enter: bool = True) -> bool:
    args = ["send-keys", "-t", target, keys]
    if enter:
        args.append("Enter")
    ok, _ = _tmux(*args)
    return ok


def send_special(target: str, key: str) -> bool:
    """Send a named key (e.g. 'Escape', 'Enter') without literal interpretation."""
    ok, _ = _tmux("send-keys", "-t", target, key)
    return ok


def send_raw(target: str, data: bytes) -> bool:
    """Send raw keystroke bytes to the pane verbatim (terminal path). `-H` sends
    hex byte values, so control chars / escape sequences (arrows, ctrl-C) pass
    through exactly as typed instead of being interpreted as key names."""
    hex_bytes = [f"{b:02x}" for b in data]
    if not hex_bytes:
        return True
    ok, _ = _tmux("send-keys", "-t", target, "-H", *hex_bytes)
    return ok


def resize_window(target: str, cols: int, rows: int) -> bool:
    """Resize the tmux window so the agent's TUI reflows to the browser pane."""
    ok, _ = _tmux("resize-window", "-t", target, "-x", str(cols), "-y", str(rows))
    return ok


def capture_pane(target: str) -> str:
    ok, out = _tmux("capture-pane", "-t", target, "-p")
    return out if ok else ""


def capture_screen(target: str) -> bytes:
    """The pane's CURRENT visible screen with colors/escapes (`-e`), as bytes —
    for an attaching terminal to repaint the live state instantly instead of
    replaying the whole scrollback.

    Trailing blank rows are dropped: the pane (120x40) is usually taller than
    the browser's xterm viewport, and painting the full pane height scrolls
    short static output out of view on attach. Lines are joined with CRLF —
    subprocess text mode normalized the pane's newlines to bare LF, which in a
    raw terminal moves down without returning to column 0."""
    ok, out = _tmux("capture-pane", "-t", target, "-p", "-e")
    if not ok:
        return b""
    lines = out.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\r\n".join(lines).encode(errors="replace")


def kill_session(target: str) -> bool:
    ok, _ = _tmux("kill-session", "-t", target)
    return ok


def session_exists(target: str) -> bool:
    ok, _ = _tmux("has-session", "-t", target)
    return ok
