"""Act 3 runtime gate: launch the fake agent under the supervisor and confirm
its lifecycle (busy -> idle -> exit) shows up as events with no real LLM."""

import asyncio
import sys
from pathlib import Path

from duckterm.core.eventbus import Event, EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.runtimes.generic import GenericRuntime

FAKE_AGENT = Path(__file__).parent.parent / "fakes" / "fake_agent.py"


def collecting_bus() -> tuple[EventBus, list[Event]]:
    captured: list[Event] = []
    return EventBus(sink=captured.append), captured


def launch_command(script_lines: list[str], tmp_path: Path) -> str:
    script = tmp_path / "agent.txt"
    script.write_text("\n".join(script_lines) + "\n")
    return f"{sys.executable} {FAKE_AGENT} --script {script} --delay 0.02"


def test_supervised_agent_emits_full_lifecycle(tmp_path: Path) -> None:
    bus, events = collecting_bus()

    async def scenario() -> None:
        orch = Orchestrator(bus)
        cmd = launch_command(["[busy]", "[tool] build", "[idle]"], tmp_path)
        key = await orch.launch(runtime=GenericRuntime(cmd), cwd=str(tmp_path), session_key="s1")
        supervisor = orch.get(key)
        assert supervisor is not None
        await asyncio.wait_for(supervisor._task, 5)  # type: ignore[arg-type]

    asyncio.run(scenario())

    types = [e["event_type"] for e in events]
    assert types[0] == "SessionStart"
    assert types[-1] == "SessionEnd"
    # The [tool] build line produces a PreToolUse with the tool name.
    tool_events = [e for e in events if e["event_type"] == "PreToolUse" and "tool_name" in e]
    assert any(e["tool_name"] == "build" for e in tool_events)
    # The [idle] marker flips state, emitting a Stop event.
    assert "Stop" in types
    # Every event carries the session key and runtime.
    assert {e["session_key"] for e in events} == {"s1"}
    assert {e["runtime"] for e in events} == {"generic"}


def test_stop_terminates_a_running_agent(tmp_path: Path) -> None:
    bus, events = collecting_bus()

    async def scenario() -> bool:
        orch = Orchestrator(bus)
        # An agent that keeps printing for ~10s unless stopped early.
        cmd = launch_command(["[busy]"] * 200, tmp_path)
        key = await orch.launch(runtime=GenericRuntime(cmd), cwd=str(tmp_path))
        await asyncio.sleep(0.2)
        running_before = orch.get(key).running  # type: ignore[union-attr]
        await orch.stop(key)
        return running_before

    running_before = asyncio.run(scenario())
    assert running_before is True
    assert events[0]["event_type"] == "SessionStart"
    assert events[-1]["event_type"] == "SessionEnd"


def test_stalled_terminal_subscriber_is_dropped_not_grown(tmp_path: Path) -> None:
    """Backpressure: a terminal that stops reading its byte queue must be cut
    loose (with an EOF so its WS closes) instead of queuing output forever —
    a backgrounded tab on a chatty agent would otherwise grow server memory
    without bound."""
    from duckterm.core.orchestrator import SessionSupervisor
    from duckterm.runtimes.generic import GenericRuntime

    async def scenario() -> tuple[int, bool, list[bytes]]:
        bus, _ = collecting_bus()
        sup = SessionSupervisor(
            bus=bus, runtime=GenericRuntime("cat"), session_key="s", cwd=str(tmp_path)
        )
        feed = sup.subscribe_bytes()
        first = asyncio.ensure_future(feed.__anext__())
        await asyncio.sleep(0)  # let the generator register its queue

        # A chatty agent while nobody drains: more chunks than the queue cap.
        for i in range(2100):
            sup._record_bytes(b"x%d" % i)
        dropped = len(sup._byte_subs) == 0

        # The stuck reader wakes up: it can drain what was queued, then the
        # EOF sentinel ends the feed (StopAsyncIteration) instead of hanging.
        got: list[bytes] = [await first]
        try:
            while True:
                got.append(await feed.__anext__())
        except StopAsyncIteration:
            pass
        return len(got), dropped, got[-2:]

    received, dropped, _tail = asyncio.run(scenario())
    assert dropped  # unsubscribed the moment its queue filled
    assert received <= 2001  # bounded: cap + nothing after the drop


def test_launched_agent_sees_its_duckterm_session_key(tmp_path: Path) -> None:
    """The agent's hooks report under DUCKTERM_SESSION_KEY. If the spawn paths
    don't put it in the environment, hook events arrive keyless and the
    launched-only ingest drops them — no approvals, no session_id, no context
    tokens. Pin BOTH paths (tmux and raw PTY)."""
    import duckterm.core.orchestrator as orch_mod
    from duckterm.core.orchestrator import Orchestrator
    from duckterm.runtimes.generic import GenericRuntime

    async def launched_env_line(force_pty: bool) -> str:
        bus, _ = collecting_bus()
        orch = Orchestrator(bus)
        if force_pty:
            real = orch_mod.tmux.has_tmux
            orch_mod.tmux.has_tmux = lambda: False
        try:
            key = await orch.launch(
                # The 0.3s sleep lets pipe-pane attach before the echo — instant
                # output races the pipe (the attach snapshot covers that case
                # in real use; here we assert on the piped stream).
                runtime=GenericRuntime(
                    "sh -c 'sleep 0.3; echo KEY=$DUCKTERM_SESSION_KEY; sleep 2'"
                ),
                cwd=str(tmp_path),
                session_key=f"envtest-{'pty' if force_pty else 'tmux'}",
            )
        finally:
            if force_pty:
                orch_mod.tmux.has_tmux = real
        sup = orch.get(key)
        assert sup is not None
        for _ in range(80):  # up to ~4s for the echo to land
            joined = "".join(sup.output_tail())
            if f"KEY={key}" in joined:
                await orch.stop(key)
                return joined
            await asyncio.sleep(0.05)
        await orch.stop(key)
        raise AssertionError(f"KEY={key} never appeared in: {joined!r}")

    for force_pty in (False, True):
        out = asyncio.run(launched_env_line(force_pty))
        assert "KEY=envtest" in out
