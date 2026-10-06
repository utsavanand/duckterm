"""Exec the owner's shell without inherited agent identity, including tmux globals."""

import os
import sys


def clean_environment() -> None:
    for name in list(os.environ):
        if name.startswith(
            ("DUCKTERM_", "RUBBERDUCK_", "UVS_SESSION", "CODEX_SESSION")
        ) or name in {
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CODEX_THREAD_ID",
        }:
            os.environ.pop(name, None)
    # Installed DuckTerm hooks must be inert for programs launched in this shell.
    os.environ["DUCKTERM_INTERNAL"] = "1"


if __name__ == "__main__":
    clean_environment()
    shell = sys.argv[1]
    os.execv(shell, [shell, "-l"])
