"""An editable Claude input box does not mean its foreground turn has ended."""

import asyncio
from contextlib import closing
from types import SimpleNamespace

import pytest

from duckterm.core.orchestrator import SessionSupervisor
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.server import Server

ACTIVE_SCREEN = (
    "Running 1 shell command…\n"
    "Logfooding… (1m 2s · 2.9k tokens)\n"
    "────\n❯ \n────\n"
    "  auto mode on · esc to interrupt"
)


@pytest.mark.parametrize(
    "screen", [ACTIVE_SCREEN, "────\n❯ \n────", "Output includes ❯ in a code example"]
)
def test_foreground_turn_survives_input_box_repaint_and_cleanup(tmp_path, monkeypatch, screen):
    with closing(HistoryStore(tmp_path / "history.sqlite")) as history:
        server = Server(history=history)
        key = "test-foreground"
        server.bus.publish(
            {
                "session_key": key,
                "event_type": "SessionStart",
                "runtime": "claude-code",
                "test": True,
            }
        )
        server.bus.publish({"session_key": key, "event_type": "UserPromptSubmit"})
        supervisor = SessionSupervisor(
            bus=server.bus,
            runtime=ClaudeCodeRuntime(),
            session_key=key,
            cwd=str(tmp_path),
            extra={"test": True},
        )
        supervisor._scan_output(screen)
        after_repaint = history.session(key)["state"]
        fake = SimpleNamespace(running=True, visible_screen=lambda: ACTIVE_SCREEN)
        monkeypatch.setattr(server.orchestrator, "get", lambda _: fake)
        asyncio.run(server._clear_stale_waiting(history.session(key)["updated_at"] + 61_000))
        assert (after_repaint, history.session(key)["state"]) == ("busy", "busy")


def test_hooks_keep_approval_waiting_and_completed_turn_idle_despite_arrow(tmp_path):
    with closing(HistoryStore(tmp_path / "history.sqlite")) as history:
        server = Server(history=history)
        key = "test-approval"
        server.bus.publish(
            {
                "session_key": key,
                "event_type": "SessionStart",
                "runtime": "claude-code",
                "test": True,
            }
        )
        supervisor = SessionSupervisor(
            bus=server.bus,
            runtime=ClaudeCodeRuntime(),
            session_key=key,
            cwd=str(tmp_path),
            extra={"test": True},
        )
        server.bus.publish({"session_key": key, "event_type": "PermissionRequest"})
        supervisor._scan_output("Do you want to proceed?\n❯ 1. Yes\n  2. No")
        assert history.session(key)["state"] == "waiting"
        server.bus.publish({"session_key": key, "event_type": "PostToolUse"})
        supervisor._scan_output(ACTIVE_SCREEN)
        assert history.session(key)["state"] == "busy"
        server.bus.publish({"session_key": key, "event_type": "Stop"})
        supervisor._scan_output("────\n❯ \n────")
        assert history.session(key)["state"] == "idle"


def test_explicit_dialog_fallback_still_requests_owner_input(tmp_path):
    with closing(HistoryStore(tmp_path / "history.sqlite")) as history:
        server = Server(history=history)
        key = "test-dialog"
        supervisor = SessionSupervisor(
            bus=server.bus,
            runtime=ClaudeCodeRuntime(),
            session_key=key,
            cwd=str(tmp_path),
            extra={"test": True},
        )
        supervisor._scan_output("│ Do you want to proceed?\n│ ❯ 1. Yes\n│ 2. No")
        assert history.session(key)["state"] == "waiting"
