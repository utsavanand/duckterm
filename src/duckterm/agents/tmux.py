"""Thin wrapper over tmux for persistent, controllable agent sessions.

A tmux-backed session survives the Duckterm server restarting and gives a
clean way to inject keystrokes (used by the approval workflow). We isolate our
sessions on a dedicated tmux socket so they never collide with the user's own
tmux server.

Ported from uv-suite's watchtower tmux service. All calls are synchronous
subprocess; drive them from async code via asyncio.to_thread.
"""

import shutil
import subprocess

from duckterm.helpers import instance

_PREFIX = "rd_"


def socket_name() -> str:
    """The tmux socket namespace, per instance. Explicit DUCKTERM_TMUX_SOCKET
    wins — tests and the e2e harness set it so their panes never mix with the
    user's real sessions and can be swept wholesale (kill-server) afterwards.
    Otherwise it derives from DUCKTERM_INSTANCE, so a per-instance socket keeps
    reconcile() from ever adopting (and killing) another instance's live panes.

    Before per-instance sockets, e2e runs leaked their cat/fixture panes onto
    the user's socket; ~100 leftovers slowed every tmux call enough to flake the
    terminal specs."""
    return instance.tmux_socket()


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


def spawn(session_id: str, command: str, cwd: str, env: dict[str, str] | None = None) -> str:
    """Create a detached tmux session running `command` in `cwd`. Returns the
    tmux target name. Raises ValueError when tmux itself refuses — before
    this, a failed spawn 'succeeded' silently and the session just appeared
    as terminated with an empty terminal and no explanation.

    `env` entries reach the agent's environment (tmux new-session -e). This
    is how DUCKTERM_SESSION_KEY gets in — without it the agent's hooks report
    under claude's own session_id and the launched-only ingest drops those
    events as foreign."""
    target = target_for(session_id)
    env_args: list[str] = []
    for k, v in (env or {}).items():
        env_args += ["-e", f"{k}={v}"]
    # `-x/-y` set the initial size; a detached session otherwise defaults to
    # 80x24, which mismatches the browser pane and garbles a TUI's wrapping.
    ok, err = _tmux(
        "new-session", "-d", "-s", target, "-x", "120", "-y", "40", "-c", cwd, *env_args, command
    )
    if not ok:
        raise ValueError(f"tmux failed to start the session: {err.strip() or 'unknown error'}")
    # window-size manual: without it tmux sizes the window to the LARGEST/LATEST
    # attached client (none, for a detached session), so resize-window from the
    # browser is ignored. Manual makes our resize authoritative.
    _tmux("set-option", "-t", target, "window-size", "manual")
    return target


def spawn_piped(
    session_id: str, command: str, cwd: str, pipe_path: str, env: dict[str, str] | None = None
) -> str:
    """Spawn a detached session and stream its pane output to `pipe_path` from
    the start, so live output isn't missed. Returns the tmux target."""
    target = spawn(session_id, command, cwd, env)
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


def capture_screen(target: str, history_lines: int = 2000) -> bytes:
    """The pane's screen WITH its scrollback history (colors/escapes intact,
    `-e`), as bytes — what an attaching terminal paints so the current state
    is at the bottom and the wheel has real history above it. Without the
    history, output that scrolled off the tmux pane before (or between)
    attaches simply didn't exist in the browser and scrollback felt broken.

    `-S -N` starts N lines back into tmux's history (clamped by tmux to what
    exists); pass history_lines=0 for just the visible screen. Trailing blank
    rows are dropped — the pane (120x40) is usually taller than the browser
    viewport, and painting the padding scrolls short output out of view.
    Lines are joined with CRLF: subprocess text mode normalized the pane's
    newlines to bare LF, which in a raw terminal never returns to column 0."""
    args = ["capture-pane", "-t", target, "-p", "-e"]
    if history_lines:
        args += ["-S", f"-{history_lines}"]
    ok, out = _tmux(*args)
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
