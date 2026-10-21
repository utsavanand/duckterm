"""Share passive terminal liveness probes without caching action-time checks."""

import asyncio
import sys

from duckterm.agents import tmux


class TailLiveness:
    """One on-demand snapshot per second for an orchestrator's output tails.

    Positive observations can delay exit detection by up to one extra second.
    A missing name is checked fresh so a snapshot predating launch cannot end
    a new tail. Discovery failure is unknown, never evidence that all panes died.
    """

    def __init__(self) -> None:
        self._targets: set[str] | None = None
        self._expires = 0.0
        self._socket = ""
        self._refresh: asyncio.Task[None] | None = None

    async def _read(self, socket: str) -> None:
        try:
            self._targets = await asyncio.to_thread(tmux.session_targets)
        except (OSError, RuntimeError) as exc:
            self._targets = None
            print(f"[duckterm] tail liveness unavailable: {exc}", file=sys.stderr)
        self._socket = socket
        self._expires = asyncio.get_running_loop().time() + 1.0

    async def exists(self, target: str) -> bool:
        loop = asyncio.get_running_loop()
        socket = tmux.socket_name()
        while self._socket != socket or loop.time() >= self._expires:
            if self._refresh is None or self._refresh.done():
                self._refresh = asyncio.create_task(self._read(socket))
            # Cancelling one tail must not cancel the observation other tails
            # are awaiting or start a second subprocess while it is in flight.
            await asyncio.shield(self._refresh)
            socket = tmux.socket_name()
        if self._targets is None or target in self._targets:
            return True
        return await asyncio.to_thread(tmux.session_exists, target)
