"""OpenCode adapter with verified native preparation and explicit unsupported actions."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from duckterm.runtimes.generic import GenericRuntime
from duckterm.runtimes.opencode_protocol import OpenCodeError, model_parts
from duckterm.runtimes.preparation import LaunchPreparation

if TYPE_CHECKING:
    from duckterm.persistence.history import HistoryStore


class OpenCodeRuntime(GenericRuntime):
    name = "opencode"
    requires_launch_preparation = True

    def __init__(self, command: str) -> None:
        self._argv = shlex.split(command) or ["opencode"]
        self.model = ""
        if len(self._argv) == 3 and self._argv[1] == "--model":
            model_parts(self._argv[2])
            self.model = self._argv[2]
        elif len(self._argv) != 1:
            raise OpenCodeError(
                "OpenCode launch accepts a binary and optional --model provider/model"
            )

    def launch_command(self, *, cwd: Path, session_key: str, initial_prompt: str) -> list[str]:
        raise OpenCodeError("OpenCode requires controlled native preparation")

    def command_for_history(self, argv: list[str]) -> str:
        # Ephemeral native ports and project routing belong to preparation,
        # not the user command reconstructed by session listing and relay.
        return shlex.join(self._argv)

    def restore_command(self, *, cwd: Path, session_key: str) -> list[str]:
        raise OpenCodeError("OpenCode restart has not passed native acceptance yet")

    def detect_state(self, recent_output: str) -> None:
        # Generic marker-like text in a model response must never drive state.
        return None

    def tool_in(self, recent_output: str) -> None:
        return None

    async def prepare_launch(
        self,
        *,
        cwd: Path,
        session_key: str,
        initial_prompt: str,
        env: dict[str, str],
        history: HistoryStore | None,
    ) -> LaunchPreparation:
        from duckterm.runtimes.opencode_process import OpenCodeProcess

        if history is None:
            raise OpenCodeError("OpenCode requires durable session history")
        if not cwd.is_dir():
            raise OpenCodeError("OpenCode project folder does not exist")
        process = OpenCodeProcess(
            self._argv[0], self.model, cwd, session_key, initial_prompt, env, history
        )
        await process.configure()
        return process

    async def prepare_recovery(
        self, *, cwd: Path, session_key: str, env: dict[str, str], history: HistoryStore | None
    ) -> LaunchPreparation:
        from duckterm.runtimes.opencode_process import OpenCodeProcess

        if history is None:
            raise OpenCodeError("OpenCode recovery requires durable session history")
        return await OpenCodeProcess.recover(cwd, session_key, env, history)
