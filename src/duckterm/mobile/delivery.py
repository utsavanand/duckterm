"""Bounded terminal operations for one explicitly authorized phone message."""

import subprocess

from duckterm.agents import tmux


def command(target: str, *args: str) -> str:
    result = subprocess.run(
        tmux.client_command(args[0], "-t", tmux.exact_target(target, pane=True), *args[1:]),
        capture_output=True,
        timeout=1.5,
    )
    if result.returncode:
        raise OSError("Terminal unavailable")
    return result.stdout.decode(errors="replace")


def identity(target: str) -> str:
    result = command(target, "display-message", "-p", "#{pane_id}:#{pane_pid}:#{pane_dead}").strip()
    if not result.startswith("%") or not result.endswith(":0") or len(result) > 100:
        raise OSError("Terminal unavailable")
    return result


def screen(target: str) -> str:
    return command(target, "capture-pane", "-p", "-e")


def write(target: str, data: bytes) -> None:
    # One command, exact pane, literal hex bytes. No shell, shared paste buffer,
    # key names supplied by the phone, or retry after a possibly partial write.
    result = subprocess.run(
        tmux.client_command(
            "send-keys",
            "-t",
            tmux.exact_target(target, pane=True),
            "-H",
            *(f"{byte:02x}" for byte in data),
        ),
        capture_output=True,
        timeout=1.5,
    )
    if result.returncode:
        raise OSError("Terminal delivery uncertain")
