"""Active orchestration: spawn agents in a PTY and own their lifecycle.

A SessionSupervisor runs one agent process, reads its output, classifies state
via the runtime's detect_state, and emits events into the EventBus. It reuses
the existing event vocabulary (SessionStart / PreToolUse / Stop / Notification /
SessionEnd) so the same derive_state and dashboard logic apply — there is no
second state machine.

PTY rather than plain pipes so interactive agents (which check isatty) behave
the same as in a real terminal.
"""

import asyncio
import contextlib
import errno
import os
import pty
import re
import shlex
import shutil
import signal
import sys
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator
from pathlib import Path

from duckterm.agents import tmux, tmux_stream
from duckterm.core import events
from duckterm.core.eventbus import EventBus
from duckterm.git.worktrees import WorktreeManager
from duckterm.helpers import paths, session_credentials, session_instructions
from duckterm.helpers.pane_log import completion_for
from duckterm.helpers.private_files import private_write
from duckterm.llm.summarizer import build_prompt, mechanical_summary, summarize
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.base import AgentRuntime, SessionState, plain_screen

# State -> the event_type whose derive_state yields that state. One vocabulary.
_TAIL_TICK_BYTES = 64 * 1024
_TAIL_ACTIVE_SLEEP = 0.025
_TAIL_LIVENESS_INTERVAL = 1.0
_TAIL_DRAIN_TIMEOUT = 5.0
_SCREEN_SCAN_INTERVAL = 0.25

_STATE_EVENT = {
    "busy": events.PRE_TOOL_USE,
    "idle": events.STOP,
    "waiting": events.NOTIFICATION,
}


# Focus (CSI I / CSI O) and mouse reports that xterm.js sends on its own when
# an agent enables those modes. Codex enables focus reporting, so merely
# clicking into or away from its pane emitted input; Oracle read that as
# possible typing and never nudged a session the owner had looked at.
_TERMINAL_REPORTS = re.compile(rb"\x1b\[(?:[IO]|<[\d;]*[Mm]|M[\s\S]{3})")


def is_terminal_report(data: bytes) -> bool:
    return not _TERMINAL_REPORTS.sub(b"", data)


class SessionSupervisor:
    def __init__(
        self,
        *,
        bus: EventBus,
        runtime: AgentRuntime,
        session_key: str,
        cwd: str,
        initial_prompt: str = "",
        extra: dict[str, object] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.bus = bus
        self.runtime = runtime
        self.session_key = session_key
        self.cwd = cwd
        self.initial_prompt = initial_prompt
        self._extra = extra or {}
        self._env = {**session_credentials.launch_env(session_key), **(env or {})}
        self._proc: asyncio.subprocess.Process | None = None
        self._state: SessionState = "busy"
        self._screen_pending: deque[str] = deque(maxlen=32)
        self._next_screen_scan = 0.0
        self._task: asyncio.Task[None] | None = None
        self._primary_fd: int | None = None  # PTY master, for writing input
        self._secondary_fd: int | None = None  # retain until final child output is drained
        self._tmux_target: str | None = None  # set when tmux-backed
        self._pipe_path: str = ""  # tmux pane output file
        self._output = deque[str](maxlen=2000)  # recent output lines, for the UI
        self._output_subs: set[asyncio.Queue[str]] = set()
        # Raw PTY bytes for the terminal (xterm.js): the undecoded stream with
        # ANSI/cursor codes intact. A separate consumer from the line view above
        # — _record_output feeds detect_state off decoded text; _record_bytes
        # feeds the terminal off raw bytes. Same PTY, two views, no interference.
        self._byte_tail = deque[bytes](maxlen=2000)  # recent raw chunks, for replay
        self._byte_subs: set[asyncio.Queue[bytes]] = set()
        # Terminal keystrokes: drained by one task so writes stay ordered.
        self._input_queue: asyncio.Queue[tuple[bytes, asyncio.Future[bool] | None]] | None = None
        self._input_task: asyncio.Task[None] | None = None
        self._last_input = 0.0
        # Wall-clock ms of the owner's last keystroke, for Oracle: no nudge is
        # pasted while someone may be typing into this terminal.
        self.last_owner_input_ms = 0

    def _emit(self, event_type: str, **fields: object) -> None:
        self.bus.publish(
            {
                "event_type": event_type,
                "session_key": self.session_key,
                "source_app": Path(self.cwd).name or self.session_key,
                "cwd": self.cwd,
                "runtime": self.runtime.name,
                # The orchestrator only ever runs sessions Duckterm launched.
                "launched": True,
                **self._extra,
                **fields,
            }
        )

    async def start(self) -> None:
        prompt = session_instructions.launch_prompt(
            self.runtime.name,
            self.session_key,
            self.initial_prompt,
            home=Path(self._env["DUCKTERM_SESSION_TOKEN_FILE"]).parent.parent,
            cwd=Path(self.cwd),
        )
        argv = self.runtime.launch_command(
            cwd=Path(self.cwd), session_key=self.session_key, initial_prompt=prompt
        )
        # Fail before publishing a session row for predictable launch errors.
        if not Path(self.cwd).is_dir():
            raise ValueError(f"project folder does not exist: {self.cwd}")
        if (
            not argv
            or shutil.which(argv[0], path=self._env.get("PATH", os.environ.get("PATH"))) is None
        ):
            raise ValueError(f"command not found: {argv[0] if argv else '(empty)'}")
        # Register and enroll synchronously before the child can use its inbox.
        self._emit(events.SESSION_START, command=shlex.join(argv))
        try:
            if await asyncio.to_thread(tmux.has_tmux):
                await self._start_tmux(argv)
            else:
                await self._start_pty(argv)
        except BaseException as exc:
            self._emit(
                events.SESSION_END,
                lifecycle="archived",
                launch_error=f"{type(exc).__name__}: {exc}",
            )
            if isinstance(exc, OSError):
                raise ValueError(f"could not start agent: {exc}") from exc
            raise

    async def _start_pty(self, argv: list[str]) -> None:
        primary, secondary = pty.openpty()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.cwd,
                stdin=secondary,
                stdout=secondary,
                stderr=secondary,
                start_new_session=True,
                # The agent's hooks report under THIS key. Without it their
                # events arrive keyless and the launched-only ingest drops
                # them — no approvals, no session_id, no context tokens.
                env={**os.environ, "DUCKTERM_SESSION_KEY": self.session_key, **self._env},
            )
        except BaseException as exc:
            # No supervisor owns these descriptors until spawn succeeds.
            os.close(secondary)
            os.close(primary)
            if isinstance(exc, FileNotFoundError):
                raise ValueError(f"command not found: {argv[0]}") from exc
            raise
        self._secondary_fd = secondary
        self._primary_fd = primary
        self._task = asyncio.create_task(self._pump(primary))

    async def _start_tmux(self, argv: list[str]) -> None:
        """Run the agent inside tmux so it survives the server restarting. Output
        streams to a pipe file we tail; input goes via tmux send-keys."""
        command = shlex.join(argv)
        self._pipe_path = str(paths.home() / "panes" / f"{self.session_key}.log")
        Path(self._pipe_path).parent.mkdir(parents=True, exist_ok=True)
        private_write(Path(self._pipe_path), "")
        self._tmux_target = await asyncio.to_thread(
            tmux.spawn_piped,
            self.session_key,
            command,
            self.cwd,
            self._pipe_path,
            env={"DUCKTERM_SESSION_KEY": self.session_key, **self._env},
        )
        self._task = asyncio.create_task(self._tail_pipe())

    async def reattach(self) -> None:
        """Reconnect to an already-running tmux session after a server restart.
        Re-tails its pipe and resumes state/output without re-spawning."""
        self._tmux_target = tmux.target_for(self.session_key)
        self._pipe_path = str(paths.home() / "panes" / f"{self.session_key}.log")
        if not Path(self._pipe_path).exists():
            Path(self._pipe_path).parent.mkdir(parents=True, exist_ok=True)
            private_write(Path(self._pipe_path), "")
        self._task = asyncio.create_task(self._tail_pipe())

    async def _tail_pipe(self) -> None:
        """Follow the tmux pane's output file, applying the same state/tool
        detection as the PTY pump. Ends when the tmux session is gone. Always
        emits SessionEnd, even if the loop raises — otherwise a crash here would
        leave the session stuck 'live' forever with no signal of why."""
        assert self._tmux_target is not None
        target = self._tmux_target
        path = Path(self._pipe_path)
        completion = None
        ending = False
        writer_finished = False
        drain_deadline = 0.0
        output_error = None
        try:
            completion = completion_for(path)
            # Read the pipe in BINARY so the terminal gets the pane's raw bytes
            # verbatim — text mode would translate the CR-LF tmux writes into bare
            # LF (universal newlines), and xterm.js needs the \r to return to
            # column 0 (otherwise output marches diagonally down the screen).
            # Read from the START of the pipe, not seek-to-end. The pipe is
            # truncated fresh when the session spawns, so reading from byte 0
            # captures the agent's STARTUP output (a TUI's whole initial screen).
            # Seeking to end dropped everything printed before this tail loop got
            # going — for a fast-starting agent like claude that's the entire
            # interface, leaving the browser a blank, unusable terminal.
            # Adaptive poll: 25ms while output is flowing (a keystroke's echo
            # shows up next tick — 150ms here was most of the typing lag the
            # terminal felt), backing off to 200ms after 5s of quiet so an
            # idle session costs ~5 file reads a second, not 40.
            last_output = 0.0
            next_liveness = 0.0
            loop = asyncio.get_running_loop()
            fh = path.open("rb")
            try:
                while True:
                    # A constantly growing pane must yield even if EOF is never
                    # reached. Keep byte chunks unchanged for terminal replay.
                    remaining = _TAIL_TICK_BYTES
                    while remaining:
                        chunk = fh.read(min(4096, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        last_output = loop.time()
                        self._record_bytes(chunk)
                        for raw_line in chunk.decode(errors="replace").splitlines():
                            self._observe_output(raw_line + "\n")
                    self._scan_pending_output(loop.time())
                    if not remaining:
                        await asyncio.sleep(_TAIL_ACTIVE_SLEEP)
                        continue
                    # The bounded writer rotates by rename. Drain the old inode,
                    # then follow the replacement without replaying the old file.
                    try:
                        rotated = path.stat().st_ino != os.fstat(fh.fileno()).st_ino
                    except FileNotFoundError:
                        rotated = False
                    if rotated:
                        fh.close()
                        fh = path.open("rb")
                        await asyncio.sleep(_TAIL_ACTIVE_SLEEP)
                        continue
                    if ending:
                        if writer_finished:
                            self._scan_pending_output(loop.time(), force=True)
                            break  # final read AFTER writer completion / liveness probe
                        if completion is not None and completion.exists():
                            if completion.read_text() != "complete":
                                output_error = "Terminal output writer failed while draining"
                            writer_finished = True
                            continue
                        if loop.time() >= drain_deadline:
                            output_error = (
                                "Terminal output writer did not confirm EOF within 5 seconds"
                            )
                            writer_finished = True
                            continue
                        await asyncio.sleep(_TAIL_ACTIVE_SLEEP)
                        continue
                    # Liveness is independent of display latency: spawning tmux
                    # on every empty 25ms poll dominated many-session CPU cost.
                    if loop.time() >= next_liveness:
                        if not await asyncio.to_thread(tmux.session_exists, target):
                            ending = True
                            writer_finished = completion is None
                            drain_deadline = loop.time() + _TAIL_DRAIN_TIMEOUT
                            continue
                        next_liveness = loop.time() + _TAIL_LIVENESS_INTERVAL
                    active = max(last_output, self._last_input)
                    await asyncio.sleep(_TAIL_ACTIVE_SLEEP if loop.time() - active < 5 else 0.2)
            finally:
                fh.close()
        except Exception as e:  # noqa: BLE001 — boundary: a background task
            output_error = f"Terminal output capture failed: {e}"
        finally:
            if output_error:
                print(f"[duckterm] {self.session_key}: {output_error}", file=sys.stderr)
            self._close_byte_subs()
            self._emit(
                events.SESSION_END, **({"output_error": output_error} if output_error else {})
            )

    async def _pump(self, primary: int) -> None:
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()
        os.set_blocking(primary, False)
        loop.add_reader(primary, ready.set)
        exited = asyncio.create_task(self._proc.wait()) if self._proc else None
        if exited is not None:
            exited.add_done_callback(lambda _: ready.set())
        pending = ""
        remaining = _TAIL_TICK_BYTES
        try:
            while True:
                try:
                    raw = os.read(primary, 4096)
                except BlockingIOError:
                    # macOS can discard unread PTY bytes when its last slave
                    # closes. Retain our slave until the child's final bytes
                    # have been read, then let EOF/EIO terminate the reader.
                    if (
                        self._proc is not None
                        and self._proc.returncode is not None
                        and self._secondary_fd is not None
                    ):
                        os.close(self._secondary_fd)
                        self._secondary_fd = None
                        continue
                    ready.clear()
                    if self._screen_pending:
                        try:
                            await asyncio.wait_for(
                                ready.wait(), max(0.001, self._next_screen_scan - loop.time())
                            )
                        except TimeoutError:
                            self._scan_pending_output(loop.time())
                    else:
                        await ready.wait()
                    continue
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break  # Linux PTY EOF; all preceding reads were recorded.
                if not raw:
                    break
                self._record_bytes(raw)
                pending += raw.decode(errors="replace")
                *lines, pending = pending.split("\n")
                for line in lines:
                    self._observe_output(line + "\n")
                self._scan_pending_output(loop.time())
                remaining -= len(raw)
                if remaining <= 0:
                    await asyncio.sleep(0)
                    remaining = _TAIL_TICK_BYTES
            if pending:
                self._observe_output(pending)
            self._scan_pending_output(loop.time(), force=True)
        except Exception as error:  # noqa: BLE001 — boundary: a background task
            print(f"[duckterm] output pump for {self.session_key} failed: {error}", file=sys.stderr)
        finally:
            loop.remove_reader(primary)
            os.close(primary)
            self._primary_fd = None
            if self._secondary_fd is not None:
                os.close(self._secondary_fd)
                self._secondary_fd = None
            if exited is not None:
                exited.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await exited
            await self._finish()

    def _observe_output(self, line: str) -> None:
        self._record_output(line)
        if self.runtime.hook_spec is not None:
            # Hooks provide detailed events; screen parsing is a bounded fallback.
            # Retain recent screen evidence without retaining the whole repaint.
            self._screen_pending.append(line[-2048:])
        else:
            # Generic agents use explicit per-line protocol markers; preserve
            # every tool/state transition for these hookless runtimes.
            self._scan_output(line)

    def _scan_pending_output(self, now: float, *, force: bool = False) -> None:
        if self._screen_pending and (force or now >= self._next_screen_scan):
            screen = "".join(self._screen_pending)
            self._screen_pending.clear()
            self._next_screen_scan = now + _SCREEN_SCAN_INTERVAL
            self._scan_output(screen)

    def _scan_output(self, output: str) -> None:
        tool = self.runtime.tool_in(output)
        if tool is not None:
            self._emit(events.PRE_TOOL_USE, tool_name=tool)
        new_state = self.runtime.detect_state(output)
        if new_state is not None and new_state != self._state:
            self._state = new_state
            self._emit(_STATE_EVENT[new_state])

    def _record_output(self, line: str) -> None:
        self._output.append(line)
        for queue in self._output_subs:
            try:
                queue.put_nowait(line)
            except asyncio.QueueFull:
                # Slow SSE reader: drop its oldest line — the line view is
                # context, losing some under pressure beats growing forever.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(line)

    def output_tail(self, limit: int = 500) -> list[str]:
        lines = list(self._output)
        return lines[-limit:] if limit < len(lines) else lines

    async def subscribe_output(self) -> AsyncGenerator[str, None]:
        """Yield output lines as the agent emits them. Replays the recent tail
        first so a late subscriber sees context."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2000)
        for line in self.output_tail():
            queue.put_nowait(line)
        self._output_subs.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._output_subs.discard(queue)

    def _record_bytes(self, chunk: bytes) -> None:
        self._byte_tail.append(chunk)
        for queue in list(self._byte_subs):
            try:
                queue.put_nowait(chunk)
            except asyncio.QueueFull:
                # A terminal that stopped reading (backgrounded/stalled tab)
                # must not grow server memory without bound. Cut it loose:
                # make room, push the EOF sentinel so its WS closes when the
                # client wakes up, and let it reconnect to a fresh snapshot.
                self._byte_subs.discard(queue)
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(b"")

    def _close_byte_subs(self) -> None:
        """Signal EOF to every attached terminal. Without this, a terminal WS
        attached when the session stops would hang silently forever (its queue
        just never fills again) instead of closing — and the browser's
        reconnect loop, which is what picks up a Resume's NEW supervisor, only
        runs after a close. b'' is the sentinel; a PTY read never yields it."""
        for queue in self._byte_subs:
            queue.put_nowait(b"")

    async def subscribe_bytes(self) -> AsyncGenerator[bytes, None]:
        """Yield raw PTY bytes as the agent emits them, for an xterm.js terminal.
        On attach, repaint the CURRENT screen (not the whole scrollback): for
        tmux, clear + capture-pane of the live pane; for a PTY, a small recent
        tail. Replaying 2000 chunks of history made the terminal redraw its entire
        backlog every time you (re)attached or switched tabs."""
        if self._tmux_target is not None:
            # Snapshot and output must share tmux's own timeline. A spool-file
            # offset cannot exclude bytes still buffered upstream of the file.
            feed = tmux_stream.stream(self._tmux_target)
            try:
                async for chunk in feed:
                    yield chunk
            finally:
                await feed.aclose()
            return
        # Bounded: ~2000 chunks ≈ 8MB of 4KB reads. _record_bytes drops the
        # subscriber (with an EOF) if it ever fills — see backpressure there.
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2000)
        snapshot = self._attach_snapshot()
        if snapshot:
            queue.put_nowait(snapshot)
        self._byte_subs.add(queue)
        try:
            while True:
                chunk = await queue.get()
                if chunk == b"":  # EOF sentinel: the session ended
                    break
                yield chunk
        finally:
            self._byte_subs.discard(queue)

    def _attach_snapshot(self) -> bytes:
        """A bounded replay for a directly owned PTY (tmux uses its control stream)."""
        # PTY snapshots and live chunks are both recorded on this event loop.
        recent = list(self._byte_tail)[-40:]
        return b"".join(recent)

    def screen_text(self, lines: int = 30) -> str:
        """Recent output as plain text, for the fleet digest. tmux: capture the
        live pane — the pipe tail misses output that raced pipe-pane's attach;
        PTY: the decoded output tail."""
        if self._tmux_target is not None and tmux.session_exists(self._tmux_target):
            screen = tmux.capture_screen(self._tmux_target, history_lines=0).decode(
                errors="replace"
            )
            rows = [r for r in plain_screen(screen).splitlines() if r.strip()]
            if rows:
                return "\n".join(rows[-lines:])
        return "".join(self.output_tail(lines)).strip()

    def visible_screen(self) -> str:
        """The visible tmux screen with escapes intact, or "" for PTY-backed
        sessions, whose raw byte stream is not a screen."""
        if self._tmux_target is None or not self.running:
            return ""
        return tmux.capture_screen(self._tmux_target, history_lines=0).decode(errors="replace")

    def resize(self, cols: int, rows: int) -> bool:
        """Resize the agent's terminal so its TUI reflows to the pane. PTY: set
        the window size on the master fd (TIOCSWINSZ). tmux: resize the window."""
        if self._tmux_target is not None and self.running:
            return tmux.resize_window(self._tmux_target, cols, rows)
        if self._primary_fd is not None and self.running:
            import fcntl
            import struct
            import termios

            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._primary_fd, termios.TIOCSWINSZ, winsize)
            return True
        return False

    def write_bytes(self, data: bytes) -> bool:
        """Write raw bytes to the agent's stdin — the terminal path. Unlike
        write_input (line/key semantics for the approval flow), this passes
        keystrokes through verbatim so the agent's TUI sees exactly what was
        typed (arrow keys, ctrl chars, partial input)."""
        if self._tmux_target is not None and self.running:
            return tmux.send_raw(self._tmux_target, data)
        if self._primary_fd is not None and self.running:
            os.write(self._primary_fd, data)
            return True
        return False

    def queue_bytes(self, data: bytes) -> None:
        """Order-preserving, non-blocking keystroke path for the terminal WS.
        tmux send-keys is a ~10ms subprocess — running it inline on the event
        loop stalled every session's I/O on each keypress. A single drain task
        does the writes off-loop, one at a time, so ordering is exact ('ab'
        can never land as 'ba', which a thread pool wouldn't guarantee)."""
        self._enqueue_input(data)

    async def write_queued_bytes(self, data: bytes) -> bool:
        """Deliver owner feedback after earlier keystrokes and report its result."""
        result: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._enqueue_input(data, result)
        return await result

    def _enqueue_input(self, data: bytes, result: asyncio.Future[bool] | None = None) -> None:
        if self._input_task is not None and (
            self._input_task.done() or self._input_task.cancelling()
        ):
            if result is not None:
                result.set_result(False)
            return
        self._last_input = time.monotonic()
        if not is_terminal_report(data):
            self.last_owner_input_ms = int(time.time() * 1000)
        if self._input_queue is None:
            self._input_queue = asyncio.Queue()
            self._input_task = asyncio.create_task(self._drain_input())
        self._input_queue.put_nowait((data, result))

    async def _drain_input(self) -> None:
        assert self._input_queue is not None
        batch: list[tuple[bytes, asyncio.Future[bool] | None]] = []
        try:
            while True:
                batch = [await self._input_queue.get()]
                size = len(batch[0][0])
                # Drain already accepted bytes without adding a batching delay.
                # Bound each batch so busy sessions yield to other loop work.
                while size < 4096 and len(batch) < 256 and not self._input_queue.empty():
                    item = self._input_queue.get_nowait()
                    batch.append(item)
                    size += len(item[0])
                try:
                    wrote = await asyncio.to_thread(
                        self.write_bytes, b"".join(data for data, _ in batch)
                    )
                except OSError:
                    wrote = False
                for _, result in batch:
                    if result is not None and not result.done():
                        result.set_result(wrote)
                batch = []
        finally:
            # Stop/cancellation must not leave feedback requests waiting forever.
            for _, result in batch:
                if result is not None and not result.done():
                    result.set_result(False)
            self._fail_pending_input()

    def _fail_pending_input(self) -> None:
        if self._input_queue is not None:
            while not self._input_queue.empty():
                _, pending = self._input_queue.get_nowait()
                if pending is not None and not pending.done():
                    pending.set_result(False)

    def write_input(self, text: str) -> bool:
        """Write to the agent's stdin (terminal-attach / approvals). Routes to
        tmux send-keys or the PTY depending on how the session is backed."""
        self.last_owner_input_ms = int(time.time() * 1000)
        if self._tmux_target is not None and self.running:
            stripped = text.rstrip("\r\n")
            enter = text.endswith(("\r", "\n"))
            if stripped == "\x1b":  # Escape (a denial)
                return tmux.send_special(self._tmux_target, "Escape")
            return tmux.send_keys(self._tmux_target, stripped, enter=enter)
        if self._primary_fd is not None and self.running:
            os.write(self._primary_fd, text.encode())
            return True
        return False

    async def _finish(self) -> None:
        if self._proc is not None:
            await self._proc.wait()
        self._close_byte_subs()
        self._emit(events.SESSION_END)

    async def stop(self) -> None:
        if self._input_task is not None:
            self._input_task.cancel()
            # A task cancelled before its first run never enters its finally.
            self._fail_pending_input()
        if self._tmux_target is not None:
            await asyncio.to_thread(tmux.kill_session, self._tmux_target)
        elif self._proc is not None and self._proc.returncode is None:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        if self._task is not None:
            await self._task

    @property
    def running(self) -> bool:
        if self._tmux_target is not None:
            return tmux.session_exists(self._tmux_target)
        return self._proc is not None and self._proc.returncode is None


class Orchestrator:
    def __init__(
        self,
        bus: EventBus,
        worktrees: WorktreeManager | None = None,
        history: HistoryStore | None = None,
    ) -> None:
        self.bus = bus
        self.worktrees = worktrees if worktrees is not None else WorktreeManager()
        self.history = history
        self._supervisors: dict[str, SessionSupervisor] = {}

    async def reconcile(self) -> list[str]:
        """On startup, re-adopt tmux sessions that outlived a previous server
        run, and reconcile the DB against reality so dead sessions don't linger
        as zombies. Each live tmux session is matched to its DB row (for
        cwd/runtime) and re-tailed. Returns the session keys re-adopted.

        A real reboot kills the tmux server, so a launched session's pane is
        gone — but its DB row still says 'busy'/'idle', and state only advances
        on events, none of which will ever arrive. So after adopting the live
        set, we mark every launched, non-at-rest row with no live backing as
        'interrupted' (resumable — honest, not deleted). If discovery is
        unavailable, leave stored states alone rather than assume death."""
        from duckterm.harnesses import infer_runtime, runtime_for

        adopted: list[str] = []
        # Missing tmux on a GUI app's PATH is not evidence that its panes died.
        if not await asyncio.to_thread(tmux.has_tmux):
            print("[duckterm] skipping reconciliation: tmux unavailable", file=sys.stderr)
            return adopted
        try:
            live_keys = await asyncio.to_thread(tmux.list_duckterm_sessions)
        except (OSError, RuntimeError) as exc:
            print(f"[duckterm] skipping reconciliation: {exc}", file=sys.stderr)
            return adopted
        for key in live_keys:
            if key in self._supervisors:
                continue
            row = self.history.session(key) if self.history else None
            cwd = str(row.get("cwd") or ".") if row else "."
            # Adopt with the session's own harness. A generic adopter stamps
            # its lifecycle events runtime=generic, which overwrote the row and
            # made Restart/Change model/Resume unable to verify the conversation.
            # The adopter never launches, so it is built with a no-op command;
            # parsing the stored command could fail on quoted paths.
            command = str(row.get("command") or "") if row else ""
            name = (row.get("runtime") if row else None) or infer_runtime(command)
            supervisor = SessionSupervisor(
                bus=self.bus, runtime=runtime_for(name, "true"), session_key=key, cwd=cwd
            )
            await supervisor.reattach()
            self._supervisors[key] = supervisor
            adopted.append(key)
            if self.history is not None:
                self.history.recover_interrupted(key)

        if self.history is not None:
            # Everything actually live right now: freshly adopted + anything a
            # still-running server already supervises.
            live = set(adopted) | set(self._supervisors)
            reconciled = self.history.stale_launched(live)
            for key in reconciled:
                self.history.set_state(key, "interrupted", now=int(time.time() * 1000))
            if reconciled:
                print(
                    f"reconciled {len(reconciled)} session(s) whose backing died "
                    f"(marked interrupted, resumable): {', '.join(reconciled)}"
                )
        return adopted

    async def launch(
        self,
        *,
        runtime: AgentRuntime,
        cwd: str | None = None,
        session_key: str | None = None,
        prompt: str = "",
        repo_path: str | None = None,
        branch: str | None = None,
        base: str | None = None,
        parent_session_key: str | None = None,
        compare_group: str | None = None,
        name: str | None = None,
        env: dict[str, str] | None = None,
        test: bool = False,
        record_intention: bool = True,
    ) -> str:
        """Launch a supervised agent. If repo_path is given, the agent runs in a
        fresh git worktree on `branch` (default: a branch named for the session),
        forked from `base` (default: repo HEAD); otherwise it runs in `cwd`.
        `parent_session_key` records fork lineage."""
        key = session_key or uuid.uuid4().hex
        extra: dict[str, object] = {"test": test}
        if parent_session_key is not None:
            extra["parent_session_key"] = parent_session_key
        if compare_group is not None:
            extra["compare_group"] = compare_group
        if name:
            extra["name"] = name
        run_cwd = cwd

        worktree = None
        if repo_path is not None:
            wt_branch = branch or f"duckterm/{key[:8]}"
            worktree = self.worktrees.add(Path(repo_path), wt_branch, base=base)
            run_cwd = str(worktree.path)
            extra |= {
                "repo_path": str(worktree.repo_path),
                "worktree_path": str(worktree.path),
                "branch": worktree.branch,
                # Label by the repo, not the worktree dir (which is the branch key).
                "source_app": worktree.repo_path.name,
            }
        if run_cwd is None:
            raise ValueError("launch requires either cwd or repo_path")

        agent_env = dict(env or {})
        if self.history is not None:
            agent_env["DUCKTERM_SESSION_TOKEN_FILE"] = str(
                session_credentials.credential_path(key, self.history.session_api.credential_dir)
            )
        supervisor = SessionSupervisor(
            bus=self.bus,
            runtime=runtime,
            session_key=key,
            cwd=run_cwd,
            initial_prompt=prompt,
            extra=extra,
            env=agent_env,
        )
        self._supervisors[key] = supervisor
        try:
            await supervisor.start()
        except BaseException:
            # A failed spawn (e.g. a typo'd command -> ValueError) must not leave
            # a git worktree + branch and a dead supervisor entry behind; those
            # accreted on every failed launch. Roll both back, then re-raise so
            # the caller still turns it into a 400.
            # Startup may fail after creating an output task. Settle it before
            # callers restore a prior harness on this same card; a late EOF
            # would otherwise overwrite the restored runtime.
            try:
                await supervisor.stop()
            finally:
                if self._supervisors.get(key) is supervisor:
                    self._supervisors.pop(key, None)
                if worktree is not None:
                    with contextlib.suppress(Exception):
                        self.worktrees.remove_by_worktree(worktree.path, delete_branch=True)
            raise
        # A resume's synthetic prompt (nudge / reconstructed notes) must not
        # overwrite the session's original intention on its card.
        if self.history is not None and prompt and record_intention:
            self.history.set_intention(key, prompt)
        if self.history is not None and supervisor._task is not None:

            def _on_done(task: asyncio.Task[None], k: str = key) -> None:
                # Surface a crash in the supervisor task — a done-callback that
                # ignores task.exception() would let it vanish silently.
                if not task.cancelled() and task.exception() is not None:
                    print(
                        f"[duckterm] session {k} task ended with error: " f"{task.exception()}",
                        file=sys.stderr,
                    )
                try:
                    self._write_summary(k)
                except Exception as e:  # noqa: BLE001 — boundary: DB + summarizer
                    print(f"[duckterm] summary for {k} failed: {e}", file=sys.stderr)

            supervisor._task.add_done_callback(_on_done)
        return key

    def _write_summary(self, key: str) -> None:
        """Write the outcome summary after a session ends. Runs the (possibly
        slow) summarizer off the event loop so it never stalls the bus."""
        if self.history is None:
            return
        row = self.history.session(key)
        if row is None:
            return
        intention = str(row.get("intention") or "")
        events_summary = self.history.events_summary(key)
        transcript = self._transcript_text(key, row)
        result = summarize(build_prompt(intention, transcript, events_summary))
        outcome = result.text or mechanical_summary(intention, events_summary)
        self.history.set_outcome(key, outcome)

    def _transcript_text(self, key: str, row: dict[str, object]) -> str:
        """Read the runtime's transcript if it has one; else empty (the generic
        runtime, which makes the summarizer fall back to the activity digest)."""
        supervisor = self._supervisors.get(key)
        session_id = self.history.session_id_for(key) if self.history else None
        cwd = row.get("cwd")
        if supervisor is None or session_id is None or not cwd:
            return ""
        path = supervisor.runtime.locate_transcript(cwd=Path(str(cwd)), session_id=session_id)
        if path is None:
            return ""
        from duckterm.runtimes.claude_code import parse_transcript

        records = parse_transcript(path)
        return "\n".join(f"{r['role']}: {r['text']}" for r in records)

    async def stop(self, session_key: str) -> bool:
        """Terminate a supervised session. Returns False if it isn't one we run
        (e.g. a watched session, which we don't own)."""
        supervisor = self._supervisors.get(session_key)
        if supervisor is None:
            return False
        await supervisor.stop()
        return True

    def get(self, session_key: str) -> SessionSupervisor | None:
        """The live supervisor for a session, or None if Duckterm didn't
        launch it (so there's no PTY/tmux we own)."""
        return self._supervisors.get(session_key)

    def inject_key(self, session_key: str, key: str) -> bool:
        """Send a symbolic key (e.g. '1', 'Escape') to a live session's stdin.
        Used by the approval workflow to answer a permission prompt. Only works
        for sessions Duckterm launched (it owns their PTY)."""
        supervisor = self._supervisors.get(session_key)
        if supervisor is None:
            return False
        text = {"Escape": "\x1b", "Enter": "\r"}.get(key, key + "\r")
        # Approval callbacks run on the event loop. Queue in the same ordered
        # drain as terminal input rather than synchronously waiting on tmux.
        supervisor.queue_bytes(text.encode())
        return True
