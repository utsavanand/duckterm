"""A pty-owned codex session whose screen shows work must not be reported as
'waiting' — codex approvals answered in the terminal fire no hook, so the DB
state can stay 'waiting' for an entire long tool run (the 17-minute bug)."""

import asyncio
import json
import tempfile
from pathlib import Path

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


class _W:
    def __init__(self) -> None:
        self.data = b""

    def write(self, b: bytes) -> None:
        self.data += b

    async def drain(self) -> None:
        pass


class _StubSupervisor:
    def __init__(self, screen: str) -> None:
        self._screen = screen

    def screen_text(self, lines: int = 0) -> str:
        return self._screen


def _waiting_codex_server(screen: str) -> Server:
    server = Server(history=HistoryStore(Path(tempfile.mkdtemp()) / "db.sqlite"))
    server.bus.publish(
        {"event_type": "SessionStart", "session_key": "S", "runtime": "codex", "cwd": "/tmp"}
    )
    server.bus.publish({"event_type": "PermissionRequest", "session_key": "S"})
    server.orchestrator._supervisors["S"] = _StubSupervisor(screen)  # type: ignore[assignment]
    return server


def _listed_state(server: Server) -> str:
    w = _W()
    asyncio.run(server._sessions(w))  # type: ignore[arg-type]
    body = json.loads(w.data.split(b"\r\n\r\n", 1)[1])
    return body["sessions"][0]["state"]


def test_waiting_flips_to_busy_when_screen_shows_work() -> None:
    server = _waiting_codex_server("Working (17m 36s • esc to interrupt)")
    assert _listed_state(server) == "busy"


def test_waiting_stays_when_screen_shows_the_prompt() -> None:
    server = _waiting_codex_server(
        "Would you like to run the following command?\nPress enter to confirm"
    )
    assert _listed_state(server) == "waiting"


def test_claude_code_is_never_screen_overridden() -> None:
    server = Server(history=HistoryStore(Path(tempfile.mkdtemp()) / "db.sqlite"))
    server.bus.publish(
        {
            "event_type": "SessionStart",
            "session_key": "S",
            "runtime": "claude-code",
            "cwd": "/tmp",
        }
    )
    server.bus.publish({"event_type": "PermissionRequest", "session_key": "S"})
    server.orchestrator._supervisors["S"] = _StubSupervisor("✻ Baking… (2s)")  # type: ignore[assignment]
    assert _listed_state(server) == "waiting"
