"""asyncio HTTP/1.1 server.

    POST /events              ingest one JSON event; returns the stamped event
    GET  /events              last 100 events as JSON (polling fallback)
    GET  /sessions            persisted session rows, incl. terminated (SQLite)
    GET  /tree                fork lineage: nodes with parent_session_key
    GET  /approvals           pending permission requests awaiting a decision
    POST /approvals/:id/decide  answer an approval {decision: approve|deny}
    POST /sessions/launch     spawn a supervised agent {command, cwd, ...}
    POST /sessions/compare    launch one prompt as N variants side by side
    POST /sessions/:key/fork  fork a session: child worktree off parent's branch
    POST /sessions/:key/fork-conversation  branch the Claude conversation (--fork-session)
    POST /sessions/:key/stop  terminate a supervised agent
    DELETE /sessions/:key     remove a session and its events/metrics/checkpoints
    POST /sessions/clear-terminated  delete all terminated sessions
    POST /sessions/:key/checkpoint   record what was done (prompts/files/tools/git + summary)
    GET  /sessions/:key/checkpoints   list checkpoint records
    POST /sessions/:key/spotlight     apply worktree changes onto the main checkout
    GET  /sessions/:key/diff          git diff of the session's worktree
    GET  /sessions/:key/output        SSE: live agent output (PTY) lines
    GET  /sessions/:key/terminal      WebSocket: raw PTY bytes <-> keystrokes/resize (xterm.js)
    POST /sessions/:key/input         write to the agent's stdin (terminal-attach)
    POST /snapshots           bundle recently-active sessions to disk
    GET  /snapshots           list snapshots
    GET  /snapshots/:id       fetch a snapshot manifest
    POST /snapshots/:id/sessions/:key/restore  relaunch a session in a terminal
    GET  /stream              SSE: {type:"init", events:[...]} then per-event frames
    GET  /ws                  WebSocket: same event stream, bidirectional
    GET  /                    liveness; carries the X-Duckterm self-probe header

Hand-rolled over asyncio rather than a framework: routing is trivial and SSE
wants direct control of the response stream. Zero runtime dependencies.
"""

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from duckterm import connectors, suites, zsh_themes
from duckterm.agents import tmux
from duckterm.agents.terminal import available_terminals, open_in_terminal
from duckterm.core import events, oracle, progress
from duckterm.core.approvals import ApprovalRegistry
from duckterm.core.backup_jobs import BackupJobs
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.core.session_api import MAX_BODY_BYTES, APIError
from duckterm.git import gitdetect
from duckterm.git.spotlight import spotlight_to_main
from duckterm.git.worktrees import GitError
from duckterm.harnesses import infer_runtime, runtime_for
from duckterm.helpers import (
    browse,
    instance,
    paths,
    security,
    session_credentials,
    session_instructions,
)
from duckterm.llm.suggest import Correction, suggest_rules
from duckterm.llm.summarizer import summarize
from duckterm.persistence import backup_sync
from duckterm.persistence.checkpoints import build_checkpoint, write_markdown
from duckterm.persistence.digests import DigestStore
from duckterm.persistence.history import HistoryStore
from duckterm.persistence.snapshots import SnapshotManager, restore_command_for
from duckterm.runtimes.base import AT_REST_STATES, AgentRuntime
from duckterm.transport.httpio import (
    KEEPALIVE_SECONDS,
    MAX_REQUEST_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    SELF_PROBE_HEADER,
    dashboard_dir,
)
from duckterm.transport.httpio import parse_request_line as _parse_request_line
from duckterm.transport.httpio import read_body as _read_body
from duckterm.transport.httpio import read_headers as _read_headers
from duckterm.transport.httpio import write_file as _write_file
from duckterm.transport.httpio import write_json as _write_json
from duckterm.transport.httpio import write_response as _write_response
from duckterm.transport.httpio import write_sse as _write_sse
from duckterm.transport.websocket import (
    close_frame,
    encode_binary_frame,
    encode_text_frame,
    handshake_response,
    ping_frame,
    read_frame,
    read_frame_opcode,
)

# How long the blocking hook polls for a decision before giving up (the
# duckterm-hook.sh DEADLINE). A blocking approval older than this whose session
# has moved on is abandoned and gets swept from "Needs human".
_BLOCKING_POLL_MS = 180_000


# Sent with every native resume: the conversation survives a dead terminal but
# out-of-band state (dev servers, background jobs) does not, and a resumed agent
# otherwise assumes everything it started is still running.
_RESUME_NUDGE = (
    "This session was resumed after its terminal stopped. Anything running "
    "outside the conversation (dev servers, watchers, background jobs) died "
    "with it — re-verify before assuming, then continue where you left off."
)


def _build_runtime(name: str | None, command: str) -> AgentRuntime:
    # Resolve through the harness registry — the single source of truth for which
    # agents exist. infer_runtime() guesses from the command when name is unset.
    return runtime_for(name or infer_runtime(command), command)


class Route:
    """One routing rule: match a (method, path) and invoke a handler. A path
    matches exactly, or by prefix+suffix for routes with a :segment in the
    middle (e.g. POST /sessions/:key/fork). `call` adapts to each handler's
    arguments so the handlers themselves stay simple."""

    def __init__(
        self,
        method: str,
        matcher: str,
        call: "RouteCall",
        *,
        prefix: str | None = None,
        suffix: str | None = None,
    ) -> None:
        self.method = method
        self.matcher = matcher  # exact path, or "" when prefix/suffix used
        self.prefix = prefix
        self.suffix = suffix
        self.call = call

    def matches(self, method: str, path: str) -> bool:
        if method != self.method:
            return False
        if self.prefix is not None and self.suffix is not None:
            return path.startswith(self.prefix) and path.endswith(self.suffix)
        if self.prefix is not None:
            return path.startswith(self.prefix)
        return path == self.matcher

    def segment(self, path: str) -> str:
        """The :segment captured between prefix and suffix (or '' / the prefix
        remainder for prefix-only routes)."""
        if self.prefix is None:
            return ""
        end = -len(self.suffix) if self.suffix else len(path)
        return path[len(self.prefix) : end]


# Each handler receives (server, reader, writer, headers, body, segment) and
# uses only what it needs. Grouped by concern.
RouteCall = Any  # an async callable; kept loose to allow per-route adapters


def _mid(prefix: str, suffix: str) -> dict[str, str]:
    return {"prefix": prefix, "suffix": suffix}


# fmt: off
_ROUTES: list[Route] = [
    Route("POST", "", lambda s, r, w, h, b, seg: s._introduce_collaboration(w, seg, send=False),
          **_mid("/sessions/", "/collaboration/instructions")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._introduce_collaboration(w, seg),
          **_mid("/sessions/", "/collaboration/introduce")),
    # ── ingest ──
    Route("POST", "/events", lambda s, r, w, h, b, seg: s._ingest(w, b)),
    Route("POST", "/heartbeat", lambda s, r, w, h, b, seg: s._heartbeat(w, b)),
    # ── query ──
    Route("GET", "/events", lambda s, r, w, h, b, seg: s._recent(w)),
    Route("GET", "/sessions", lambda s, r, w, h, b, seg: s._sessions(w)),
    Route("GET", "/tree", lambda s, r, w, h, b, seg: s._tree(w)),
    Route("GET", "", lambda s, r, w, h, b, seg: s._browse(w, seg), prefix="/browse"),
    Route("GET", "", lambda s, r, w, h, b, seg: s._branches(w, seg), prefix="/branches"),
    Route("POST", "/agents-md/suggest", lambda s, r, w, h, b, seg: s._suggest_agents_md(w, b)),
    Route("GET", "", lambda s, r, w, h, b, seg: s._read_agents_md(w, seg), prefix="/agents-md"),
    Route("POST", "/agents-md", lambda s, r, w, h, b, seg: s._write_agents_md(w, b)),
    Route("GET", "", lambda s, r, w, h, b, seg: s._read_file(w, seg), prefix="/file"),
    Route("POST", "/file", lambda s, r, w, h, b, seg: s._write_file(w, b)),
    Route("POST", "/paste-image", lambda s, r, w, h, b, seg: s._paste_image(w, h, b)),
    Route("GET", "/approvals", lambda s, r, w, h, b, seg: s._list_approvals(w)),
    Route("GET", "", lambda s, r, w, h, b, seg: s._approval_decision(w, seg),
          **_mid("/approvals/", "/decision")),
    Route("GET", "/terminals", lambda s, r, w, h, b, seg: s._terminals(w)),
    Route("GET", "/snapshots", lambda s, r, w, h, b, seg: s._list_snapshots(w)),
    Route("GET", "", lambda s, r, w, h, b, seg: s._diff(w, seg), **_mid("/sessions/", "/diff")),
    Route("GET", "", lambda s, r, w, h, b, seg: s._session_events(w, seg),
          **_mid("/sessions/", "/events")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._session_enroll(w, seg, b),
          **_mid("/sessions/", "/collaboration")),
    Route("GET", "", lambda s, r, w, h, b, seg: s._messages(w, seg),
          **_mid("/sessions/", "/messages")),
    Route("GET", "", lambda s, r, w, h, b, seg: s._list_annotations(w, seg),
          **_mid("/sessions/", "/annotations")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._add_annotation(w, seg, b),
          **_mid("/sessions/", "/annotations")),
    Route("GET", "", lambda s, r, w, h, b, seg: s._list_checkpoints(w, seg),
          **_mid("/sessions/", "/checkpoints")),
    Route("GET", "", lambda s, r, w, h, b, seg: s._session_digest(w, seg),
          **_mid("/sessions/", "/digest")),
    # ── control ──
    Route("POST", "/sessions/launch", lambda s, r, w, h, b, seg: s._launch(w, b)),
    Route("POST", "/sessions/compare", lambda s, r, w, h, b, seg: s._compare(w, b)),
    Route("POST", "/fleet/ask", lambda s, r, w, h, b, seg: s._fleet_ask(w, b)),
    Route("POST", "/sessions/clear-terminated",
          lambda s, r, w, h, b, seg: s._clear_terminated(w)),
    Route("GET", "/zsh-themes", lambda s, r, w, h, b, seg: s._list_zsh_themes(w)),
    # ── installable harnesses (suites like uv-suite) ──
    Route("GET", "/harnesses", lambda s, r, w, h, b, seg: s._list_harnesses(w)),
    Route("POST", "/harnesses/register", lambda s, r, w, h, b, seg: s._register_harness(w, b)),
    Route("POST", "", lambda s, r, w, h, b, seg: s._install_harness(w, seg, b),
          **_mid("/harnesses/", "/install")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._uninstall_harness(w, seg, b),
          **_mid("/harnesses/", "/uninstall")),
    Route("GET", "", lambda s, r, w, h, b, seg: s._harness_contents(w, seg),
          **_mid("/harnesses/", "/contents")),
    Route("DELETE", "", lambda s, r, w, h, b, seg: s._deregister_harness(w, seg),
          prefix="/harnesses/"),
    # ── connectors (GitHub, Railway, … — credentials + MCP install) ──
    Route("GET", "/connectors", lambda s, r, w, h, b, seg: s._list_connectors(w)),
    Route("POST", "", lambda s, r, w, h, b, seg: s._enable_connector(w, seg, b),
          **_mid("/connectors/", "/enable")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._disable_connector(w, seg),
          **_mid("/connectors/", "/disable")),
    # ── left-panel folders ──
    Route("GET", "/session-inbox-counts", lambda s, r, w, h, b, seg: s._inbox_counts(w, h)),
    Route("GET", "/folders", lambda s, r, w, h, b, seg: s._list_folders(w)),
    Route("POST", "/folders", lambda s, r, w, h, b, seg: s._create_folder(w, b)),
    Route("PATCH", "", lambda s, r, w, h, b, seg: s._move_folder(w, seg, b),
          prefix="/folders/"),
    Route("DELETE", "", lambda s, r, w, h, b, seg: s._delete_folder(w, seg),
          prefix="/folders/"),
    Route("PATCH", "", lambda s, r, w, h, b, seg: s._update_session(w, seg, b),
          prefix="/sessions/"),
    Route("DELETE", "", lambda s, r, w, h, b, seg: s._delete_session(w, seg, b),
          prefix="/sessions/"),
    Route("POST", "", lambda s, r, w, h, b, seg: s._fork_conversation(w, seg, b),
          **_mid("/sessions/", "/fork-conversation")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._fork(w, seg, b),
          **_mid("/sessions/", "/fork")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._promote(w, seg, b),
          **_mid("/sessions/", "/promote")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._stop(w, seg),
          **_mid("/sessions/", "/stop")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._resume(w, seg),
          **_mid("/sessions/", "/resume")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._archive(w, seg),
          **_mid("/sessions/", "/archive")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._checkpoint(w, seg, b),
          **_mid("/sessions/", "/checkpoint")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._spotlight(w, seg),
          **_mid("/sessions/", "/spotlight")),
    Route("POST", "", lambda s, r, w, h, b, seg: s._input(w, seg, b),
          **_mid("/sessions/", "/input")),
    Route("POST", "/approvals", lambda s, r, w, h, b, seg: s._register_approval(w, b)),
    Route("POST", "", lambda s, r, w, h, b, seg: s._decide_approval(w, seg, b),
          **_mid("/approvals/", "/decide")),
    Route("POST", "/snapshots", lambda s, r, w, h, b, seg: s._create_snapshot(w)),
    Route("POST", "", lambda s, r, w, h, b, seg: s._restore(w, seg),
          **_mid("/snapshots/", "/restore")),
    # ── streams ──
    Route("GET", "", lambda s, r, w, h, b, seg: s._output(r, w, seg),
          **_mid("/sessions/", "/output")),
    Route("GET", "", lambda s, r, w, h, b, seg: s._terminal(r, w, h, seg),
          **_mid("/sessions/", "/terminal")),
    Route("GET", "/stream", lambda s, r, w, h, b, seg: s._stream(r, w)),
    Route("GET", "/ws", lambda s, r, w, h, b, seg: s._websocket(r, w, h)),
    # ── single session (prefix-only; AFTER /sessions/:key/* sub-routes) ──
    Route("GET", "", lambda s, r, w, h, b, seg: s._get_session(w, seg), prefix="/sessions/"),
    # ── snapshot fetch (prefix-only; keep AFTER /snapshots/:id/restore) ──
    Route("GET", "", lambda s, r, w, h, b, seg: s._get_snapshot(w, seg), prefix="/snapshots/"),
    # ── dashboard (prefix-only catch for / and /assets/*) ──
    Route("GET", "/", lambda s, r, w, h, b, seg: s._dashboard(w, "/")),
    Route("GET", "", lambda s, r, w, h, b, seg: s._dashboard(w, "/assets/" + seg),
          prefix="/assets/"),
    Route("GET", "/favicon.svg", lambda s, r, w, h, b, seg: s._dashboard(w, "/favicon.svg")),
    Route("GET", "/favicon.ico", lambda s, r, w, h, b, seg: s._dashboard(w, "/favicon.ico")),
]
# fmt: on


class Server:
    def __init__(self, bus: EventBus | None = None, history: HistoryStore | None = None) -> None:
        self.history = history if history is not None else HistoryStore()
        self.bus = bus if bus is not None else EventBus(sink=self._sink)
        self.orchestrator = Orchestrator(self.bus, history=self.history)
        self.snapshots = SnapshotManager(self.history)
        self._backup_jobs: BackupJobs | None = None
        self.approvals = ApprovalRegistry(self.orchestrator.inject_key)
        self._oracle_nudges: dict[str, oracle.Nudge] = {}
        # Per-session (last digest ts, event_count) — debounces progress refreshes.
        self._progress_marks: dict[str, tuple[int, int]] = {}
        # Durable digest archive (deliverables/learnings/next actions as rows).
        self.digests = DigestStore()
        self.token = security.load_or_create_token()
        # transcript path -> (mtime, context_tokens): /sessions is fetched
        # often and an unchanged transcript can't have new usage.
        self._context_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    # Activity that means a session moved past an *earlier* permission prompt:
    # any of these arriving AFTER a request means it was answered and the agent
    # continued (otherwise an answered-in-terminal request would linger as fake
    # "needs human" noise). Time-gated so the tool that IS the request — Claude
    # emits PermissionRequest and that tool's PreToolUse in the same tick —
    # doesn't clear its own pending approval.
    _RESOLVES_APPROVAL = {
        events.PRE_TOOL_USE,
        events.POST_TOOL_USE,
        events.USER_PROMPT_SUBMIT,
        events.STOP,
        events.SESSION_END,
    }

    def _sink(self, event: dict[str, Any]) -> None:
        """Fan a published event to the durable store and the approval registry.
        Enrich watched sessions with git state detected from their cwd, so they
        too can show repo/branch and be forked into a worktree."""
        self._enrich_git(event)
        self.history.record(event)
        self.approvals.from_event(event)
        if event.get("event_type") == events.STOP:
            key = event.get("session_key") or event.get("session_id")
            if key:
                self._maybe_refresh_progress(str(key))
        if event.get("event_type") in self._RESOLVES_APPROVAL:
            key = event.get("session_key") or event.get("session_id")
            if key:
                ts = int(event.get("_ts", 0))
                self.approvals.drop_session_before(str(key), ts)
                # A blocking approval older than the hook's poll deadline whose
                # session has since moved on is abandoned — the hook stopped
                # polling. Clear it so it doesn't linger in "Needs human".
                self.approvals.drop_abandoned_blocking(str(key), ts, _BLOCKING_POLL_MS)

    def _enrich_git(self, event: dict[str, Any]) -> None:
        """If an event has a cwd but no repo/branch yet (a watched session),
        detect git state from the cwd and add it. Cached per cwd, so this is
        effectively once per session, not per event."""
        cwd = event.get("cwd")
        if not cwd or event.get("repo_path") or event.get("branch"):
            return
        info = gitdetect.detect(str(cwd))
        if info is not None:
            event["repo_path"] = info.repo_path
            event["branch"] = info.branch
            event.setdefault("source_app", info.repo_name)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                request_line = await reader.readline()
                if not request_line:
                    return
                method, path = _parse_request_line(request_line)
                headers = await _read_headers(reader)
                if not security.host_allowed(headers.get("host", "")):
                    await _write_response(writer, 403, "non-local Host refused")
                    return
                if not security.origin_allowed(headers):
                    await _write_response(writer, 403, "cross-origin request refused")
                    return
                size = int(headers.get("content-length", "0"))
                limit = MAX_BODY_BYTES if path.startswith("/api/v1/session/") else MAX_REQUEST_BYTES
                if size < 0 or size > limit:
                    await _write_json(writer, 413, {"error": "request body too large"})
                    return
                body = await _read_body(reader, headers)
            await self._dispatch(method, path, reader, writer, headers, body)
        except (ValueError, TimeoutError) as exc:
            with contextlib.suppress(OSError):
                await _write_json(writer, 400, {"error": str(exc) or "request timed out"})
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def _dispatch(
        self,
        method: str,
        path: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        # Cross-origin requests are refused outright: a malicious web page must
        # not be able to drive this server (which executes commands and touches
        # the filesystem) even though it binds localhost. Same-origin requests
        # and CLI tools send no Origin and pass.
        if not security.origin_allowed(headers):
            await _write_response(writer, 403, "cross-origin request refused")
            return
        if path.startswith("/api/v1/session/"):
            try:
                status, result = self.history.session_api.handle(method, path, headers, body)
            except APIError as exc:
                status, result = exc.status, {"error": str(exc)}
            await _write_json(writer, status, result)
            return
        # A presented agent credential must never fall through to owner routes.
        if headers.get("authorization", "").startswith("Bearer "):
            await _write_json(
                writer, 403, {"error": "session credentials cannot access owner routes"}
            )
            return
        # State-changing requests additionally require the per-install secret,
        # which a blind CSRF can't read and therefore can't forge. GETs (the
        # dashboard, static assets, read-only data) stay open so the browser can
        # load the UI; they're already protected by the same-origin check above.
        if method != "GET" and not security.token_valid(headers, self.token):
            await _write_json(writer, 401, {"error": "missing or invalid token"})
            return

        if path == "/backup" and method in {"GET", "PUT", "POST"}:
            await self._backup(writer, headers, method, body)
            return
        pin_match = re.fullmatch(r"/sessions/([A-Za-z0-9._-]+)/pins(?:/([a-f0-9]{64}))?", path)
        if pin_match and method in {"GET", "POST", "DELETE"}:
            await self._message_pins(writer, headers, pin_match[1], pin_match[2], method, body)
            return
        inbox_path = urllib.parse.urlsplit(path)
        if method == "GET" and inbox_path.path == "/folder-interactions":
            await self._folder_inbox(writer, headers, inbox_path.query)
            return
        inbox_match = re.fullmatch(r"/sessions/([A-Za-z0-9._-]+)/inbox", inbox_path.path)
        if method == "GET" and inbox_match:
            await self._session_inbox(writer, inbox_match[1], headers, inbox_path.query)
            return
        broadcast_match = re.fullmatch(r"/folders/(.+)/broadcast", inbox_path.path)
        if method in {"GET", "POST"} and broadcast_match:
            await self._folder_broadcast(writer, headers, broadcast_match[1], method, body)
            return
        for route in self._routes():
            if route.matches(method, path):
                try:
                    await route.call(self, reader, writer, headers, body, route.segment(path))
                except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
                    raise  # client went away — the outer handler ignores these
                except Exception as e:  # noqa: BLE001 — the fail-loudly boundary
                    # An unhandled handler bug used to kill the connection
                    # silently: the browser saw a network error, the log saw
                    # nothing. Now the server log gets the full traceback and
                    # the client gets a real 500 with the reason.
                    print(f"[duckterm] {method} {path} failed: {e}", file=sys.stderr)
                    traceback.print_exc()
                    with contextlib.suppress(OSError):
                        await _write_json(writer, 500, {"error": f"{type(e).__name__}: {e}"})
                return
        await _write_response(writer, 404, "not found")

    def _routes(self) -> list["Route"]:
        # Grouped by concern. Each Route binds a (method, matcher) to a handler
        # and declares which args it wants — keeps dispatch declarative.
        return _ROUTES

    async def _dashboard(self, writer: asyncio.StreamWriter, path: str) -> None:
        """Serve the built React dashboard so there's one URL. The self-probe
        header rides on every response. Falls back to a hint if not built."""
        dist = dashboard_dir()
        if dist is None:
            # No bundled UI. For an installed copy this means a broken wheel
            # (the dashboard should ship inside it) — tell them to reinstall,
            # not to run a dev build they have no source for. Only a source
            # checkout (web/ present) gets the build hint.
            web_src = Path(__file__).resolve().parents[2] / "web"
            if web_src.is_dir():
                msg = (
                    "Duckterm server is running, but the dashboard isn't built. "
                    "From the repo: cd web && npm run build (or run `duckterm dashboard`)."
                )
            else:
                msg = (
                    "Duckterm server is running, but this install is missing its "
                    "dashboard — reinstall DuckTerm (pipx reinstall duckterm)."
                )
            await _write_response(writer, 200, msg, extra_headers={SELF_PROBE_HEADER: "1"})
            return
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (dist / rel).resolve()
        if not target.is_relative_to(dist.resolve()) or not target.is_file():
            target = dist / "index.html"  # SPA fallback
        if target.name == "index.html":
            # Inject the per-install token so the dashboard's fetches can send it.
            # Same-origin script can read it; a cross-origin attacker can't.
            html = target.read_text().replace(
                "<head>",
                f'<head><meta name="duckterm-token" content="{self.token}">',
                1,
            )
            await _write_response(
                writer,
                200,
                html,
                content_type="text/html",
                extra_headers={
                    SELF_PROBE_HEADER: "1",
                    "Cache-Control": "no-store",
                    "X-Frame-Options": "DENY",
                    "Content-Security-Policy": "frame-ancestors 'none'",
                    "X-Content-Type-Options": "nosniff",
                },
            )
            return
        await _write_file(writer, target)

    async def _ingest(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        try:
            raw: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        if not isinstance(raw, dict):
            await _write_json(writer, 400, {"error": "event must be a JSON object"})
            return
        # agent_pid comes from an external hook ($PPID) and is later fed to
        # os.kill in the liveness sweep — coerce to a positive int or drop it.
        if "agent_pid" in raw:
            try:
                pid = int(raw["agent_pid"])
                raw["agent_pid"] = pid if pid > 0 else None
            except (TypeError, ValueError):
                raw["agent_pid"] = None
        # A deleted (tombstoned) session whose agent is still running keeps firing
        # hooks. Drop ALL of its events here — including SessionStart — so a
        # session you deleted stays gone: no phantom rows, no events leaking into
        # the Pulse feed. Deleted means deleted. To bring a session back that you
        # deleted by mistake, `duckterm restart` (a fresh server has no
        # tombstones, so still-running agents re-stream and reappear).
        key = raw.get("session_key") or raw.get("session_id")
        if key and self.history.is_tombstoned(str(key)):
            await _write_json(writer, 200, {"dropped": "session deleted"})
            return
        # Launched-only: the hook sends session_key only when DUCKTERM_SESSION_KEY
        # was in the agent's env — i.e. Duckterm started it. An event with only
        # the agent's own session_id comes from a session the user ran themselves;
        # watching those is Rubberduck's product, not Duckterm's, so don't let it
        # create a row here (it would be a session you can't act on or attach to).
        if not raw.get("session_key") and not (
            raw.get("session_id") and self.history.session(str(raw["session_id"]))
        ):
            await _write_json(writer, 200, {"dropped": "not a Duckterm-launched session"})
            return
        # Check before publishing Stop: folding that event changes waiting to idle.
        hook_output = self._inbox_hook_output(raw, str(key or ""))
        event = self.bus.publish(raw)
        response = dict(event)
        if hook_output:
            response["hook_output"] = hook_output
        await _write_json(writer, 200, response)

    def _inbox_hook_output(self, raw: dict[str, Any], key: str) -> dict[str, Any] | None:
        if raw.get("event_type") != events.STOP or raw.get("stop_hook_active") is not False:
            return None
        row = self.history.session(key)
        if not row or row.get("state") in {"waiting", "stopped", "archived"}:
            return None
        if any(a.session_key == key for a in self.approvals.pending()):
            return None
        runtime = _build_runtime(row.get("runtime"), row.get("command") or "")
        if not runtime.turn_end_inbox_notice:
            return None
        notice = self.history.session_api.turn_end_notice(key)
        if not notice:
            return None
        return {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": notice}}

    async def _recent(self, writer: asyncio.StreamWriter) -> None:
        await _write_json(writer, 200, {"events": self.bus.recent()})

    def _init_events(self) -> list[dict[str, object]]:
        """Events for a stream's init replay, minus any whose session has since
        been deleted. The ring buffer can still hold a deleted session's
        SessionStart (it was published before the delete), and replaying it would
        resurrect the row on a fresh page load — its tombstone Set starts empty,
        so it can't filter the event out client-side."""
        return [
            e
            for e in self.bus.recent()
            if not self.history.is_tombstoned(
                str(e.get("session_key") or e.get("session_id") or "")
            )
        ]

    async def _session_events(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        """A session's own events from the durable store, oldest first — for the
        detail-drawer timeline. (The /events ring buffer only holds the last 100
        across all sessions, so it can't back a per-session view.)"""
        await _write_json(writer, 200, {"events": self.history.events_for(session_key)})

    async def _inbox_counts(self, writer: asyncio.StreamWriter, headers: dict[str, str]) -> None:
        if not security.token_valid(headers, self.token):
            await _write_json(writer, 401, {"error": "owner credential required"})
            return
        await _write_json(writer, 200, {"counts": self.history.session_api.pending_counts()})

    async def _session_inbox(
        self, writer: asyncio.StreamWriter, session_key: str, headers: dict[str, str], query: str
    ) -> None:
        if not security.token_valid(headers, self.token):
            await _write_json(writer, 401, {"error": "owner credential required"})
            return
        try:
            params = urllib.parse.parse_qs(query)
            before = int(params["before"][0]) if "before" in params else None
            result = self.history.session_api.inbox(session_key, owner=True, before=before)
        except ValueError:
            await _write_json(writer, 400, {"error": "invalid cursor"})
            return
        except APIError as exc:
            await _write_json(writer, exc.status, {"error": str(exc)})
            return
        await _write_json(writer, 200, result)

    async def _folder_inbox(
        self, writer: asyncio.StreamWriter, headers: dict[str, str], query: str
    ) -> None:
        if not security.token_valid(headers, self.token):
            await _write_json(writer, 401, {"error": "owner credential required"})
            return
        try:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            folder = params.get("folder", [""])[0]
            if folder not in self.history.folders():
                raise APIError(404, "folder not found")
            before = int(params["before"][0]) if "before" in params else None
            result = self.history.session_api.folder_inbox(folder, before=before)
        except ValueError:
            await _write_json(writer, 400, {"error": "invalid cursor"})
            return
        except APIError as exc:
            await _write_json(writer, exc.status, {"error": str(exc)})
            return
        await _write_json(writer, 200, result)

    def _session_env(self, key: str) -> dict[str, str]:
        return {
            **session_credentials.launch_env(key),
            "DUCKTERM_SESSION_TOKEN_FILE": str(
                session_credentials.credential_path(key, self.history.session_api.credential_dir)
            ),
        }

    def _collaboration_prompt(self, runtime: str, key: str, prompt: str = "") -> str:
        return session_instructions.launch_prompt(
            runtime, key, prompt, home=self.history.session_api.credential_dir.parent
        )

    async def _introduce_collaboration(
        self, writer: asyncio.StreamWriter, session_key: str, *, send: bool = True
    ) -> None:
        row = self.history.session(session_key)
        if row is None:
            await _write_json(writer, 404, {"error": "session not found"})
            return
        if row.get("runtime") not in session_instructions.SUPPORTED_RUNTIMES:
            await _write_json(
                writer, 409, {"error": "this runtime has no supported prompt interface"}
            )
            return
        if row.get("state") in AT_REST_STATES:
            await _write_json(writer, 409, {"error": "Resume the session first"})
            return
        supervisor = self.orchestrator.get(session_key)
        if send and (row.get("state") != "idle" or supervisor is None or not supervisor.running):
            await _write_json(
                writer,
                409,
                {"error": "Introduction requires an idle agent with a connected terminal"},
            )
            return
        self.history.session_api.ensure(session_key)
        prompt = (
            session_instructions.introduction(
                session_key, home=self.history.session_api.credential_dir.parent
            )
            + "\n\nRead these instructions, then continue the user's current task."
        )
        if not send:
            await _write_json(writer, 200, {"prompt": prompt})
            return
        assert supervisor is not None
        # A user-requested follow-up, bracketed as one paste rather than many Enter presses.
        sent = supervisor.write_bytes(b"\x1b[200~" + prompt.encode() + b"\x1b[201~\r")
        await _write_json(writer, 200 if sent else 409, {"sent": sent})

    async def _session_enroll(
        self, writer: asyncio.StreamWriter, session_key: str, body: bytes
    ) -> None:
        try:
            req = json.loads(body or b"{}")
            if not isinstance(req, dict):
                raise APIError(400, "expected a JSON object")
            result = self.history.session_api.enroll(session_key, req)
        except (ValueError, UnicodeDecodeError):
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        except APIError as exc:
            await _write_json(writer, exc.status, {"error": str(exc)})
            return
        await _write_json(writer, 200, result)

    def _session_messages(self, session_key: str) -> list[dict[str, object]]:
        row = self.history.session(session_key)
        if row is None:
            return []
        session_id = self.history.session_id_for(session_key)
        cwd = row.get("worktree_path") or row.get("cwd")
        runtime_name = str(row.get("runtime") or "generic")
        runtime = _build_runtime(runtime_name, "")
        messages = runtime.messages(cwd=Path(str(cwd)), session_id=session_id) if cwd else []
        for message in messages:
            # Line numbers alone can silently point elsewhere after a rewrite.
            # Include contents and conversation scope; stale pins use their snapshot.
            identity = json.dumps(
                [runtime_name, session_id, message], sort_keys=True, ensure_ascii=True
            ).encode()
            message["message_key"] = hashlib.sha256(identity).hexdigest()
        return messages

    async def _messages(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        if self.history.session(session_key) is None:
            await _write_json(writer, 404, {"error": "no such session"})
            return
        messages = await asyncio.to_thread(self._session_messages, session_key)
        await _write_json(writer, 200, {"messages": messages})

    async def _message_pins(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str],
        session_key: str,
        message_key: str | None,
        method: str,
        body: bytes,
    ) -> None:
        if not security.token_valid(headers, self.token):
            await _write_json(writer, 401, {"error": "owner credential required"})
            return
        if self.history.session(session_key) is None:
            await _write_json(writer, 404, {"error": "no such session"})
            return
        if method == "DELETE" and message_key:
            self.history.remove_message_pin(session_key, message_key)
            await _write_json(writer, 200, {"removed": True})
            return
        if message_key or method == "DELETE":
            await _write_json(writer, 400, {"error": "invalid pin endpoint"})
            return
        if method == "GET":
            await _write_json(writer, 200, {"pins": self.history.message_pins(session_key)})
            return
        if len(body) > 4096:
            await _write_json(writer, 413, {"error": "pin request too large"})
            return
        try:
            req = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            req = None
        key = req.get("message_key") if isinstance(req, dict) else None
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
            await _write_json(writer, 400, {"error": "message_key is required"})
            return
        pins = self.history.message_pins(session_key)
        existing = next((pin for pin in pins if pin["message_key"] == key), None)
        if existing:
            await _write_json(writer, 200, {"pin": existing})
            return
        if len(pins) >= 100:
            await _write_json(
                writer, 409, {"error": "unpin a message before adding more (limit 100)"}
            )
            return
        messages = await asyncio.to_thread(self._session_messages, session_key)
        message = next((m for m in messages if m["message_key"] == key), None)
        if message is None:
            await _write_json(
                writer, 409, {"error": "message changed or is unavailable; refresh Messages"}
            )
            return
        # Recheck after the transcript read yields, including session deletion.
        if self.history.session(session_key) is None:
            await _write_json(writer, 404, {"error": "no such session"})
            return
        if len(json.dumps(message).encode()) > 1024 * 1024:
            await _write_json(writer, 413, {"error": "message is too large to pin (limit 1 MiB)"})
            return
        pins = self.history.message_pins(session_key)
        if len(pins) >= 100 and not any(pin["message_key"] == key for pin in pins):
            await _write_json(
                writer, 409, {"error": "unpin a message before adding more (limit 100)"}
            )
            return
        self.history.add_message_pin(session_key, message)
        pin = next(p for p in self.history.message_pins(session_key) if p["message_key"] == key)
        await _write_json(writer, 200, {"pin": pin})

    async def _list_annotations(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        await _write_json(writer, 200, {"annotations": self.history.annotations(session_key)})

    async def _add_annotation(
        self, writer: asyncio.StreamWriter, session_key: str, body: bytes
    ) -> None:
        """Store a {quote, note} annotation AND send it back to the agent as a
        follow-up prompt, so the user's feedback on a response re-enters the
        conversation. Requires a live supervisor (the agent's stdin)."""
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        quote = (req.get("quote") or "").strip()
        note = (req.get("note") or "").strip()
        if not note:
            await _write_json(writer, 400, {"error": "note is required"})
            return
        ann_id = security.new_session_key("ann")
        self.history.add_annotation(ann_id, session_key, quote, note, int(time.time() * 1000))
        # Compose the follow-up and write it to the agent's stdin (the same path
        # the terminal uses). Quote the span so the agent knows what it's about.
        supervisor = self.orchestrator.get(session_key)
        sent = False
        if supervisor is not None:
            prompt = f'Re: "{quote}" — {note}' if quote else note
            sent = supervisor.write_bytes(prompt.encode() + b"\r")
        await _write_json(writer, 200, {"id": ann_id, "sent": sent})

    async def _heartbeat(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        """A launched tab pings here while alive. Records last_seen so the sweep
        can tell a killed tab from a quiet one."""
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        key = req.get("session_key")
        if not security.valid_session_key(key):
            await _write_json(writer, 400, {"error": "valid session_key required"})
            return
        # tty identifies which terminal the session runs in; constrain it to the
        # /dev/tty… shape so a forged ping can't store an arbitrary payload.
        tty = req.get("tty")
        if tty is not None and not security.valid_tty(tty):
            tty = None
        ok = self.history.touch(str(key), int(time.time() * 1000), tty=tty)
        await _write_json(writer, 200, {"ok": ok})

    async def _sessions(self, writer: asyncio.StreamWriter) -> None:
        sessions = self.history.sessions()
        subagents = self.history.subagents_by_session()
        for s in sessions:
            s["subagents"] = subagents.get(str(s.get("session_key") or ""), [])
            stats = self._transcript_stats_for(s)
            s["context_tokens"] = stats.get("context_tokens")
            live_model = stats.get("model")
            if live_model and live_model != s.get("model"):
                # Persist so rule scopes like "claude-code/fable-5" still match
                # after the transcript is gone (codex exposes none -> stays NULL).
                self.history.set_model(str(s["session_key"]), str(live_model))
            s["model"] = live_model or s.get("model")
            s["suites"] = self._suites_for(s.get("worktree_path") or s.get("cwd"))
            self._reconcile_waiting(s)
        await _write_json(writer, 200, {"sessions": sessions})

    def _reconcile_waiting(self, row: dict[str, Any]) -> None:
        """Before reporting a pty-owned session as 'waiting', glance at its
        actual screen. Codex answers approvals IN the terminal without firing a
        hook, so a PermissionRequest can stay the last event for the entire run
        of the approved command — the DB says 'waiting' while the screen says
        'Working (17m)'. Output-detected states are authoritative for output-
        driven runtimes; claude-code is excluded (its hooks fire per tool, so
        this bug can't happen, and its output detector is too coarse to trust)."""
        if row.get("state") != "waiting" or (row.get("runtime") or "") == "claude-code":
            return
        key = str(row.get("session_key") or "")
        sup = self.orchestrator.get(key)
        if sup is None:
            return
        runtime = _build_runtime(str(row.get("runtime") or "generic"), "")
        screen = sup.screen_text(8)
        if screen and runtime.detect_state(screen) == "busy":
            row["state"] = "busy"

    def _transcript_stats_for(self, row: dict[str, Any]) -> dict[str, Any]:
        """Live transcript-tail stats for a claude-code session: current
        context size (the checkpoint/compact signal) and the model in use."""
        if (row.get("runtime") or "") != "claude-code":
            return {}
        sid = self.history.session_id_for(str(row.get("session_key") or ""))
        cwd = row.get("worktree_path") or row.get("cwd")
        if not sid or not cwd:
            return {}
        from duckterm.runtimes.claude_code import ClaudeCodeRuntime, transcript_stats

        path = ClaudeCodeRuntime().locate_transcript(cwd=Path(str(cwd)), session_id=sid)
        if path is None:
            return {}
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        cached = self._context_cache.get(str(path))
        if cached is not None and cached[0] == mtime:
            return cached[1]
        stats = transcript_stats(path)
        self._context_cache[str(path)] = (mtime, stats)
        return stats

    def _suites_for(self, directory: object) -> list[str]:
        """Which installed harness suites (meta-harnesses, e.g. uv-suite) a
        session runs under: each registered suite with a `detect` marker is
        checked in the session's own folder, then globally (~/.claude)."""
        out: list[str] = []
        for row in self.history.harnesses():
            try:
                suite = suites.load(Path(str(row["path"])))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            if not suite.detect:
                continue
            if directory and (Path(str(directory)) / suite.detect).exists():
                out.append(f"{suite.name} (project)")
            elif suite.detect.startswith(".claude/") and (Path.home() / suite.detect).exists():
                out.append(f"{suite.name} (global)")
        return out

    async def _launch(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        command = req.get("command")
        cwd = req.get("cwd")
        repo_path = req.get("repo_path")
        if not command or (not cwd and not repo_path):
            await _write_json(
                writer, 400, {"error": "command and one of cwd/repo_path are required"}
            )
            return
        name = req.get("name")
        # An oh-my-zsh prompt theme for this session's shell (ZDOTDIR wrapper —
        # an env var alone loses to the hardcoded ZSH_THEME in ~/.zshrc).
        extra_env: dict[str, str] = {}
        if req.get("zsh_theme"):
            try:
                extra_env = zsh_themes.theme_env(req["zsh_theme"])
            except ValueError as e:
                await _write_json(writer, 400, {"error": str(e)})
                return

        # Headless: Duckterm supervises the agent invisibly (automation / CI).
        if not req.get("in_terminal", True):
            try:
                key = await self.orchestrator.launch(
                    runtime=_build_runtime(req.get("runtime"), command),
                    cwd=cwd,
                    repo_path=repo_path,
                    branch=req.get("branch"),
                    base=req.get("base"),
                    session_key=req.get("session_key"),
                    prompt=req.get("prompt", ""),
                    name=name,
                    env=extra_env,
                )
            except (GitError, ValueError) as e:
                await _write_json(writer, 400, {"error": str(e)})
                return
            # Persist the name like the terminal path does: the SessionStart
            # event carries it to LIVE dashboards, but the row is what refresh
            # and server-side readers (the fleet digest) see.
            if name or req.get("notes"):
                self.history.set_meta(key, name=name, notes=req.get("notes"))
            await _write_json(writer, 200, {"session_key": key, "opened_in_terminal": False})
            return

        # Default: open the agent in a terminal tab you can see and drive.
        run_cwd = cwd
        repo_name = None
        branch = None
        worktree_path = None
        if repo_path:
            try:
                wt = self.orchestrator.worktrees.add(
                    Path(repo_path),
                    req.get("branch") or _branch_name(name),
                    base=req.get("base") or None,
                )
            except (GitError, ValueError) as e:
                await _write_json(writer, 400, {"error": str(e)})
                return
            run_cwd = str(wt.path)
            repo_name = wt.repo_path.name
            branch = wt.branch
            worktree_path = str(wt.path)

        # Unguessable key so the input/attach endpoints can't be hit by guessing
        # a predictable `new-<timestamp>`. A caller-supplied key must be sane.
        key = req.get("session_key") or security.new_session_key("new")
        if not security.valid_session_key(key):
            await _write_json(writer, 400, {"error": "invalid session_key"})
            return
        # Inject the prompt into the agent command the same way the headless path
        # does — each runtime appends it in its own form (claude/codex positional,
        # copilot `-p`). Without this the terminal launch dropped the prompt and
        # only kept it as `intention`, so the agent opened with an empty session.
        runtime = _build_runtime(req.get("runtime"), command)
        argv = runtime.launch_command(
            cwd=Path(run_cwd),
            session_key=key,
            initial_prompt=self._collaboration_prompt(runtime.name, key, req.get("prompt", "")),
        )

        # Record a tracked row so the session shows up with its name/repo/branch.
        # The agent's hooks report under the same key (via DUCKTERM_SESSION_KEY)
        # so they update this row instead of creating a duplicate.
        self.bus.publish(
            {
                "event_type": events.SESSION_START,
                "session_key": key,
                "name": name,
                "source_app": repo_name
                or (run_cwd.rstrip("/").rsplit("/", 1)[-1] if run_cwd else key),
                "runtime": _build_runtime(req.get("runtime"), command).name,
                "cwd": str(run_cwd),
                "repo_path": repo_path,
                "worktree_path": worktree_path,
                "branch": branch,
                "intention": req.get("prompt", ""),
                "launched": True,
                # A tab launch is not attachable from the browser — the PTY
                # lives in the user's terminal app, not in Duckterm.
                "pty_owned": False,
                "command": command,
            }
        )
        opened = open_in_terminal(
            str(run_cwd),
            argv,
            app=req.get("terminal"),
            env={**self._session_env(key), **extra_env},
            heartbeat=(instance.heartbeat_url(), key),
            title=name or repo_name,
        )
        if name or req.get("notes"):
            self.history.set_meta(key, name=name, notes=req.get("notes"))
        if opened:
            self.history.mark_heartbeat(key)
        await _write_json(
            writer,
            200,
            {"session_key": key, "opened_in_terminal": opened, "command": command},
        )

    async def _fork(self, writer: asyncio.StreamWriter, parent_key: str, body: bytes) -> None:
        parent = self.history.session(parent_key)
        if parent is None:
            await _write_json(writer, 404, {"error": f"no session {parent_key}"})
            return
        if not parent.get("repo_path") or not parent.get("branch"):
            await _write_json(writer, 400, {"error": "parent has no worktree to fork from"})
            return
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        command = req.get("command") or "claude"
        repo = Path(str(parent["repo_path"]))
        branch = req.get("branch") or f"fork/{parent_key[:8]}"
        base = str(parent["branch"])

        # PTY path (the dashboard's default): the orchestrator creates the
        # worktree and supervises the agent, so the fork renders in the browser
        # terminal. carry_context swaps in the parent harness's resume command
        # so the fork continues the conversation in the isolated worktree.
        if not req.get("in_terminal", True):
            runtime_name = req.get("runtime", parent.get("runtime") or "generic")
            run_command = command
            carried = False
            if req.get("carry_context"):
                resumed = self._carry_context_argv(parent, parent_key, repo, command)
                if resumed:
                    run_command = shlex.join(resumed)
                    carried = True
            try:
                key = await self.orchestrator.launch(
                    runtime=_build_runtime(runtime_name, run_command),
                    repo_path=str(repo),
                    branch=branch,
                    base=base,
                    parent_session_key=parent_key,
                    session_key=req.get("session_key"),
                    prompt=req.get("prompt", ""),
                )
            except (GitError, ValueError) as e:
                await _write_json(writer, 400, {"error": str(e)})
                return
            self._inherit_group(parent, key)
            await _write_json(
                writer,
                200,
                {
                    "session_key": key,
                    "parent_session_key": parent_key,
                    "branch": branch,
                    "carried_context": carried,
                    "opened_in_terminal": False,
                },
            )
            return

        # Default: create the worktree and open the agent in a terminal you can
        # drive (an interactive agent like claude needs a real terminal).
        try:
            worktree = self.orchestrator.worktrees.add(repo, branch, base=base)
        except (GitError, ValueError) as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        child_key = req.get("session_key") or security.new_session_key("fork")
        if not security.valid_session_key(child_key):
            await _write_json(writer, 400, {"error": "invalid session_key"})
            return
        # Carry the parent's conversation into the new worktree, so the fork has
        # the isolated code AND the prior context — for any harness that can
        # resume (each declares its own resume command). Falls back to a fresh
        # agent when the harness has no native resume or no recorded session.
        argv = shlex.split(command)
        carried = False
        if req.get("carry_context"):
            resumed = self._carry_context_argv(parent, parent_key, repo, command)
            if resumed:
                argv = resumed
                carried = True

        fork_runtime = _build_runtime(parent.get("runtime"), shlex.join(argv))
        argv = fork_runtime.launch_command(
            cwd=worktree.path,
            session_key=child_key,
            initial_prompt=self._collaboration_prompt(
                fork_runtime.name, child_key, req.get("prompt", "")
            ),
        )
        # Record a tracked row so the fork shows its lineage. The agent's hooks
        # report under child_key (via DUCKTERM_SESSION_KEY), updating this row.
        self.bus.publish(
            {
                "event_type": events.SESSION_START,
                "session_key": child_key,
                "source_app": repo.name,
                "runtime": parent.get("runtime") or "claude-code",
                "repo_path": str(repo),
                "worktree_path": str(worktree.path),
                "branch": worktree.branch,
                "parent_session_key": parent_key,
                "intention": f"fork of {parent.get('source_app') or parent_key} ({base})",
                "launched": True,
                "pty_owned": False,
                "command": shlex.join(argv),
            }
        )
        opened = open_in_terminal(
            str(worktree.path),
            argv,
            app=req.get("terminal"),
            env=self._session_env(child_key),
            heartbeat=(instance.heartbeat_url(), child_key),
            title=worktree.branch,
        )
        if opened:
            self.history.mark_heartbeat(child_key)
        self._inherit_group(parent, child_key)
        await _write_json(
            writer,
            200,
            {
                "session_key": child_key,
                "parent_session_key": parent_key,
                "opened_in_terminal": opened,
                "worktree": str(worktree.path),
                "branch": worktree.branch,
                "command": " ".join(argv),
                "carried_context": carried,
            },
        )

    async def _promote(self, writer: asyncio.StreamWriter, session_key: str, body: bytes) -> None:
        """Create a git worktree + branch for a session that's been running in
        place (no worktree yet) — for when the user decides the work is worth
        isolating onto a branch they can publish. The repo is the session's cwd;
        the new worktree is branched off `base` (default: the repo's HEAD)."""
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        row = self.history.session(session_key)
        if row is None:
            await _write_json(writer, 404, {"error": "no such session"})
            return
        if row.get("worktree_path"):
            await _write_json(writer, 409, {"error": "session already has a worktree"})
            return
        repo_dir = row.get("repo_path") or row.get("cwd")
        if not repo_dir:
            await _write_json(writer, 400, {"error": "session has no directory to branch from"})
            return
        repo = Path(str(repo_dir))
        branch = req.get("branch") or _branch_name(row.get("name") or session_key)
        base = req.get("base") or None
        try:
            worktree = await asyncio.to_thread(
                self.orchestrator.worktrees.add, repo, branch, base=base
            )
        except (GitError, ValueError) as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        # Record the new worktree/branch on the session row so the dashboard
        # shows it and worktree-only actions (fork, spotlight) light up.
        self.bus.publish(
            {
                "event_type": events.NOTIFICATION,
                "session_key": session_key,
                "repo_path": str(repo),
                "worktree_path": str(worktree.path),
                "branch": worktree.branch,
            }
        )
        await _write_json(
            writer,
            200,
            {
                "session_key": session_key,
                "worktree": str(worktree.path),
                "branch": worktree.branch,
            },
        )

    async def _fork_conversation(
        self, writer: asyncio.StreamWriter, parent_key: str, body: bytes
    ) -> None:
        """Branch the *conversation* (not the code): run `claude --resume <id>
        --fork-session` so you can interact with the forked conversation. The
        dashboard passes in_terminal:false and gets a PTY Duckterm owns (the
        fork renders in the browser); in_terminal:true opens a real terminal
        window for API callers who want one. Only for a claude-code session
        whose Claude session_id is known."""
        parent = self.history.session(parent_key)
        if parent is None:
            await _write_json(writer, 404, {"error": f"no session {parent_key}"})
            return
        if (parent.get("runtime") or "") != "claude-code":
            await _write_json(
                writer, 400, {"error": "conversation fork is only for claude-code sessions"}
            )
            return
        cwd = str(parent.get("cwd") or ".")
        session_id = self._resumable_session_id(parent_key, cwd)
        # A session that hasn't had a conversation yet (or whose transcript is
        # gone) still forks — as a FRESH sibling session in the same folder.
        # The response says so plainly, and the server log records why.
        note = None
        if session_id:
            argv = ["claude", "--resume", session_id, "--fork-session"]
            child_key = f"convfork-{session_id[:8]}"
        else:
            note = "no conversation to fork yet — started a fresh session in the same folder"
            print(
                f"[duckterm] conversation fork of {parent_key}: no resumable Claude "
                f"conversation (none recorded, or its transcript is gone); "
                f"launching fresh in {cwd}",
                file=sys.stderr,
            )
            argv = ["claude"]
            child_key = security.new_session_key("convfork")
        req = json.loads(body or b"{}")

        if not req.get("in_terminal", True):
            key = await self.orchestrator.launch(
                runtime=_build_runtime("claude-code", shlex.join(argv)),
                cwd=cwd,
                session_key=child_key,
                parent_session_key=parent_key,
                name=f"{parent.get('name') or parent.get('source_app') or parent_key} (fork)",
            )
            self._inherit_group(parent, key)
            await _write_json(
                writer,
                200,
                {
                    "session_key": key,
                    "parent_session_key": parent_key,
                    "opened_in_terminal": False,
                    "carried_conversation": session_id is not None,
                    "note": note,
                    "command": " ".join(argv),
                    "cwd": cwd,
                },
            )
            return

        fork_title = f"{parent.get('source_app') or parent_key} (fork)"
        argv = _build_runtime("claude-code", shlex.join(argv)).launch_command(
            cwd=Path(cwd),
            session_key=child_key,
            initial_prompt=self._collaboration_prompt("claude-code", child_key),
        )

        # Record a row so the conversation fork shows its lineage.
        self.bus.publish(
            {
                "event_type": events.SESSION_START,
                "session_key": child_key,
                "source_app": parent.get("source_app") or "fork",
                "runtime": "claude-code",
                "cwd": cwd,
                "parent_session_key": parent_key,
                "intention": f"conversation fork of {parent.get('source_app') or parent_key}",
            }
        )
        opened = open_in_terminal(
            cwd,
            argv,
            app=req.get("terminal"),
            env=self._session_env(child_key),
            title=fork_title,
        )
        self._inherit_group(parent, child_key)
        await _write_json(
            writer,
            200,
            {
                "session_key": child_key,
                "parent_session_key": parent_key,
                "opened_in_terminal": opened,
                "carried_conversation": session_id is not None,
                "note": note,
                "command": " ".join(argv),
                "cwd": cwd,
            },
        )

    def _inherit_group(self, parent: dict[str, Any], child_key: str) -> None:
        """A fork belongs where its parent lives: copy the parent's folder to
        the child. Without this, forking from inside a folder scattered the
        children into Ungrouped (and a folder's grid showed one session)."""
        grp = parent.get("grp")
        if grp:
            self.history.set_meta(child_key, group=str(grp))

    def _carry_context_argv(
        self, parent: dict[str, Any], parent_key: str, repo: Path, command: str
    ) -> list[str] | None:
        """The argv to relaunch a fork *with the parent's conversation* — built
        from the parent harness's own resume command, so this works for any agent
        that can resume (Claude: --resume <id> --fork-session; Copilot:
        --resume=<id>). Returns None for harnesses with no native resume
        (codex/generic) or when no resumable session is recorded."""
        runtime = parent.get("runtime") or "generic"
        cwd = str(parent.get("cwd") or repo)
        if runtime == "claude-code":
            sid = self._resumable_session_id(parent_key, cwd)
            # --fork-session branches the conversation so the parent isn't touched.
            return ["claude", "--resume", sid, "--fork-session"] if sid else None
        if runtime == "codex":
            from duckterm.runtimes.codex import CodexRuntime

            sid = CodexRuntime().find_resumable_id(
                cwd=Path(cwd), recorded=self.history.session_id_for(parent_key)
            )
            # `codex fork` branches the rollout so the parent isn't touched.
            return ["codex", "fork", sid] if sid else None
        if runtime == "copilot":
            sid = self.history.session_id_for(parent_key)
            return [*shlex.split(command), f"--resume={sid}"] if sid else None
        # generic: no native conversation resume — can't carry context.
        return None

    def _resumable_session_id(self, parent_key: str, cwd: str) -> str | None:
        """A Claude conversation id that can actually be `--resume`d, or None if
        there's nothing resumable (see ClaudeCodeRuntime.find_resumable_id)."""
        from duckterm.runtimes.claude_code import ClaudeCodeRuntime

        return ClaudeCodeRuntime().find_resumable_id(
            cwd=Path(cwd), recorded=self.history.session_id_for(parent_key)
        )

    def _restore_session_with_resume_id(self, session: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of a snapshot session whose `session_key` is the harness's
        resumable conversation id (so its resume command works), or has
        `_no_resume` set when nothing is resumable (restore then launches
        fresh). Generic is unchanged — it doesn't resume by id."""
        runtime = session.get("runtime") or "generic"
        key = str(session.get("session_key", ""))
        cwd = str(session.get("worktree_path") or session.get("cwd") or ".")
        binary = {"claude-code": "claude", "codex": "codex", "copilot": "copilot"}.get(runtime)
        if binary is None:
            return session  # generic: no id-based resume
        resume_id = runtime_for(runtime, binary).find_resumable_id(
            cwd=Path(cwd), recorded=self.history.session_id_for(key)
        )
        out = dict(session)
        if resume_id:
            out["session_key"] = resume_id
        else:
            out["_no_resume"] = True  # restore_command_for -> fresh launch
        return out

    async def _stop(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        # An in-process supervised session has a PTY we can terminate directly.
        # A session running in the user's own terminal (duckterm run / a tab we
        # opened) isn't ours to kill — the user stops it there.
        # Persist the pause before killing the child: its SessionEnd may arrive
        # during stop(), and must not irreversibly cancel pending questions.
        if self.orchestrator.get(session_key) is not None:
            self._set_lifecycle(session_key, "stopped")
        stopped = await self.orchestrator.stop(session_key)
        # Mark it stopped (resumable) rather than terminated — Stop is a pause; the
        # worktree, branch, and conversation id are kept so Resume can continue it.
        # Publish so the change persists AND reaches connected dashboards over SSE.
        if stopped:
            self._set_lifecycle(session_key, "stopped")
            self.approvals.drop_session(session_key)
        status = 200 if stopped else 404
        await _write_json(writer, status, {"stopped": stopped, "session_key": session_key})

    def _set_lifecycle(self, session_key: str, lifecycle: str) -> None:
        """Publish a lifecycle event (stopped/archived) for a session. The bus
        sink persists it via history.record (derive_state honors the marker) and
        the SSE stream pushes it to dashboards, so a manual stop/archive updates
        the UI live instead of only on reload."""
        self.bus.publish(
            {"event_type": events.NOTIFICATION, "session_key": session_key, "lifecycle": lifecycle}
        )

    async def _resume(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        """Resume a stopped/terminated launched session: relaunch its agent in the
        saved worktree/cwd under the same session_key. For claude-code, continue
        the conversation with `--resume <claude session_id>`; other runtimes
        relaunch their command (fresh conversation if they have no native resume).
        """
        row = self.history.session(session_key)
        if row is None:
            await _write_json(writer, 404, {"error": f"no session {session_key}"})
            return
        if not row.get("heartbeat") and not row.get("launched"):
            await _write_json(
                writer, 400, {"error": "only Duckterm-launched sessions can be resumed"}
            )
            return
        # Stop is a pause; archive is final. An archived session keeps its
        # history but is done — resuming it would contradict what Archive means.
        if row.get("state") == "archived":
            await _write_json(
                writer, 400, {"error": "archived sessions can't be resumed (archive is final)"}
            )
            return
        cwd = str(row.get("worktree_path") or row.get("cwd") or ".")
        # The saved worktree/dir may be gone (deleted worktree, pruned, wiped
        # home). Relaunching into a missing dir lands the agent in $HOME with no
        # branch — refuse with a clear reason instead.
        if not Path(cwd).is_dir():
            await _write_json(
                writer,
                409,
                {
                    "error": "the session's working directory no longer exists — "
                    "its worktree was likely removed",
                    "cwd": cwd,
                },
            )
            return
        runtime = row.get("runtime") or "generic"
        argv, carried = self._resume_argv(session_key, runtime, row)
        # Report honestly whether the conversation is carried, so the UI can
        # warn — before this, resume always claimed success even when it
        # silently dropped all prior context. When it can't be carried, a
        # promptable harness at least gets reconstructed notes; and even a
        # native resume gets a nudge, because out-of-band state (dev servers,
        # background jobs) died with the terminal while the agent remembers
        # starting it.
        prompt = ""
        if runtime in session_instructions.SUPPORTED_RUNTIMES:
            prompt = _RESUME_NUDGE if carried else self._resume_brief(session_key, row)
        # Relaunch in a PTY Duckterm owns so the resumed session renders in the
        # browser terminal — even if it originally ran in the user's own tab
        # (duckterm run); clear the heartbeat flag so the row reads as
        # PTY-owned again. The supervisor's SessionStart both persists the
        # revive and reaches dashboards over SSE, lifting the stopped/archived
        # rest-state back to busy.
        await self.orchestrator.launch(
            runtime=_build_runtime(runtime, shlex.join(argv)),
            cwd=cwd,
            session_key=session_key,
            prompt=prompt,
            record_intention=False,
        )
        self.history.clear_heartbeat(session_key)
        await _write_json(
            writer,
            200,
            {
                "resumed": True,
                "session_key": session_key,
                "command": argv,
                "carried_conversation": carried,
                "context": "native" if carried else ("brief" if prompt else "none"),
            },
        )

    def _resume_argv(self, key: str, runtime: str, row: dict[str, Any]) -> tuple[list[str], bool]:
        """The command to relaunch a session, and whether it carries the
        conversation. Harnesses with a native resume (claude/codex/copilot)
        continue the recorded conversation when its transcript still exists;
        everything else relaunches the originally recorded command — a fresh
        conversation."""
        binary = {"claude-code": "claude", "codex": "codex", "copilot": "copilot"}.get(runtime)
        if binary:
            rt = runtime_for(runtime, binary)
            cwd = Path(str(row.get("worktree_path") or row.get("cwd") or "."))
            sid = rt.find_resumable_id(cwd=cwd, recorded=self.history.session_id_for(key))
            if sid:
                return rt.restore_command(cwd=cwd, session_key=sid), True
        argv = shlex.split(str(row.get("command"))) if row.get("command") else []
        if binary:
            # The recorded command is the previous launch's full argv — binary,
            # flags, and any initial prompt or resume id it started with. The
            # positionals must not replay into the relaunch: a recorded prompt
            # became argv again on the next revive and doubled every revive
            # after that (seen in the wild: a command with the collaboration
            # prompt accreted four times). Keep flags, drop positionals and any
            # stale resume pointer.
            flags = [a for a in argv[1:] if a.startswith("-") and not a.startswith("--resume")]
            argv = [binary, *flags]
        elif not argv:
            argv = ["claude"]
        return argv, False

    def _resume_brief(self, key: str, row: dict[str, Any]) -> str:
        """Reconstructed notes for a relaunch whose conversation can't be
        natively resumed. Framed as notes about a previous session, never as
        the agent's own memory — an agent handed fake memory hallucinates the
        details it lacks."""
        facts = []
        if row.get("intention"):
            facts.append(f"original task: {row['intention']}")
        if row.get("branch"):
            facts.append(f"branch: {row['branch']}")
        if row.get("outcome_summary"):
            facts.append(f"where it left off: {row['outcome_summary']}")
        else:
            facts.append(f"recorded activity: {self.history.events_summary(key)}")
        return (
            "You are taking over from a previous agent session in this "
            "directory whose conversation could not be restored. Reconstructed "
            "notes (verify before relying on them):\n- " + "\n- ".join(facts)
        )

    async def _archive(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        """Put a session away for good: history is kept, the row leaves the
        list, and it can't be resumed (archive is FINAL — stop is the pause).
        Stops its PTY first if it's still live."""
        row = self.history.session(session_key)
        if row is None:
            await _write_json(writer, 404, {"error": f"no session {session_key}"})
            return
        # Only sessions Duckterm owns can be archived. Archiving a watched
        # session would hide a row whose agent keeps running in a terminal we
        # don't control.
        if not row.get("launched"):
            await _write_json(
                writer, 400, {"error": "only Duckterm-launched sessions can be archived"}
            )
            return
        await self.orchestrator.stop(session_key)
        self._set_lifecycle(session_key, "archived")
        self.approvals.drop_session(session_key)
        await _write_json(writer, 200, {"archived": True, "session_key": session_key})

    async def _delete_session(
        self, writer: asyncio.StreamWriter, session_key: str, body: bytes
    ) -> None:
        # Stop it first if it's live (best-effort), remove its worktree (if any),
        # then drop it from the DB. If the worktree has unmerged commits and the
        # caller didn't pass force, refuse so agent work isn't silently lost.
        force = False
        with contextlib.suppress(json.JSONDecodeError):
            force = bool(json.loads(body or b"{}").get("force"))
        row = self.history.session(session_key)
        # -1 means the unmerged check failed; treat unknown as unsafe and refuse
        # (unless forced), same as if there were unmerged commits.
        unmerged = self._worktree_unmerged(row)
        if unmerged != 0 and not force:
            await _write_json(
                writer,
                409,
                {
                    "deleted": False,
                    "session_key": session_key,
                    "unmerged_commits": unmerged,
                    "unmerged_check_failed": unmerged < 0,
                    "branch": row.get("branch") if row else None,
                },
            )
            return
        deleted = await self._teardown_session(session_key, row)
        status = 200 if deleted else 404
        await _write_json(writer, status, {"deleted": deleted, "session_key": session_key})

    async def _teardown_session(self, session_key: str, row: dict[str, Any] | None) -> bool:
        """Everything a session leaves behind: supervisor/tmux pane, worktree,
        DB rows (cascade), pending approvals. Callers do the unmerged check."""
        if not await self.orchestrator.stop(session_key):
            # No supervisor (launched before a server restart and never
            # re-adopted) — kill any leftover tmux session by its canonical
            # name so DB deletes never orphan panes.
            tmux.kill_session(tmux.target_for(session_key))
        self._remove_worktree(row)
        deleted = self.history.delete_session(session_key, now=int(time.time() * 1000))
        self.approvals.drop_session(session_key)
        self.digests.delete_session(session_key)
        return deleted

    def _worktree_path_of(self, row: dict[str, Any] | None) -> Path | None:
        """The Duckterm-managed worktree for a session, or None. Guards that we
        only ever act on worktrees under our own dir, never the user's repo."""
        if row is None:
            return None
        wt = row.get("worktree_path")
        if not wt or "/.duckterm/worktrees/" not in str(wt):
            return None
        return Path(str(wt))

    def _worktree_unmerged(self, row: dict[str, Any] | None) -> int:
        """Count commits on the worktree branch not yet in main. Returns -1 when
        the git check itself fails: 'we couldn't tell' must NOT be treated as
        'zero unmerged', or a broken check would silently bypass the delete guard
        and discard the agent's work."""
        wt = self._worktree_path_of(row)
        if wt is None or not wt.exists():
            return 0
        try:
            return self.orchestrator.worktrees.unmerged_commits(wt)
        except GitError:
            return -1

    def _remove_worktree(self, row: dict[str, Any] | None) -> None:
        """Remove the git worktree + branch for a Duckterm-created session.
        Only touches worktrees under our worktrees dir; never the user's repo."""
        wt = self._worktree_path_of(row)
        if wt is None:
            return
        # The worktree itself knows its main repo (shared object store), so this
        # works whether or not the row recorded the original repo_path.
        with contextlib.suppress(GitError):
            self.orchestrator.worktrees.remove_by_worktree(wt)

    async def _update_session(
        self, writer: asyncio.StreamWriter, session_key: str, body: bytes
    ) -> None:
        """Set a user-given name, notes, and/or folder group on a session (local).
        `group: ""` ungroups; omitting a field leaves it unchanged."""
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        ok = self.history.set_meta(
            session_key,
            name=req.get("name"),
            notes=req.get("notes"),
            group=req.get("group"),
        )
        await _write_json(writer, 200 if ok else 404, {"updated": ok})

    async def _clear_terminated(self, writer: asyncio.StreamWriter) -> None:
        """Bulk delete of terminated sessions, through the same teardown as a
        single DELETE (previously this only dropped DB rows, leaving tmux
        panes and worktrees orphaned). Sessions whose worktree has unmerged
        commits are skipped, not force-deleted."""
        cleared: list[str] = []
        skipped: list[str] = []
        for key in self.history.terminated_keys():
            row = self.history.session(key)
            if self._worktree_unmerged(row) != 0:
                skipped.append(key)
                continue
            await self._teardown_session(key, row)
            cleared.append(key)
        await _write_json(
            writer, 200, {"cleared": len(cleared), "session_keys": cleared, "skipped": skipped}
        )

    # The fleet chat is digest-based on purpose: one LLM call over capped
    # per-session digests answers fleet-level questions ("who's stuck?",
    # "which sessions touched auth?") in seconds. Upgrade path if digests
    # prove too shallow for deep history questions: give the answerer tools
    # to read a named session's full transcript and diff on demand.
    _FLEET_STATES_DONE = AT_REST_STATES  # sessions past this state aren't "running"

    # ── running progress digest (deliverables / learnings / next actions) ──
    # Regenerated on turn ends (Stop), debounced so a chatty session costs at
    # most one summarizer call per window; stored on the session row so it
    # survives restarts and rides along in /sessions.
    _PROGRESS_MIN_INTERVAL_MS = 90_000
    _PROGRESS_MIN_NEW_EVENTS = 4

    def _maybe_refresh_progress(self, session_key: str) -> None:
        row = self.history.session(session_key)
        if row is None:
            return
        now = int(time.time() * 1000)
        last_ts, last_count = self._progress_marks.get(session_key, (0, 0))
        count = int(row.get("event_count") or 0)
        if now - last_ts < self._PROGRESS_MIN_INTERVAL_MS:
            return
        if count - last_count < self._PROGRESS_MIN_NEW_EVENTS:
            return
        self._progress_marks[session_key] = (now, count)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (sync test context) — the next Stop will retry
        loop.create_task(self._refresh_progress(session_key))

    async def _refresh_progress(self, session_key: str) -> None:
        row = self.history.session(session_key)
        if row is None:
            return
        transcript = await asyncio.to_thread(self._progress_transcript, row)
        if not transcript:
            return
        prior = None
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            prior = json.loads(row.get("progress") or "")
        prompt = progress.build_prompt(transcript, prior, str(row.get("intention") or ""))
        summary = await asyncio.to_thread(summarize, prompt)
        digest = progress.parse(summary.text)
        if digest is None:
            return
        now = int(time.time() * 1000)
        self.history.set_progress(session_key, json.dumps(digest), now)
        # Validate before archiving: L1 (each item makes sense on its own) +
        # L2 (compared against what's already stored) in one summarizer call.
        # A failed/garbled validation falls back to a code-only merge — the
        # store's normalized-text dedup still applies, and losing a digest to
        # a flaky validator would be worse than an occasional near-duplicate.
        existing = self.digests.items(session_key)
        verdict_reply = await asyncio.to_thread(
            summarize, progress.validate_prompt(digest, existing)
        )
        verdicts = progress.parse_verdicts(verdict_reply.text) or progress.fallback_verdicts(digest)
        self.digests.merge(session_key, verdicts["accept"], verdicts["done_next_action_ids"], now)
        self._file_digest_candidates(row)

    # A collaboration pattern seen in this many distinct sessions of one folder
    # graduates from digest observation to a proposed AGENTS.md rule.
    _DIGEST_RULE_MIN_SESSIONS = 3

    def _file_digest_candidates(self, row: dict[str, Any]) -> None:
        """The digest→AGENTS.md bridge: user_learnings that recur across
        sessions in this folder become candidate rules in its rule set. Only
        for folders that already adopted the typed format (rules.json exists) —
        the bridge must not start creating files in every watched folder. A
        human still promotes: candidates never render into AGENTS.md."""
        from duckterm.core import agents_rules
        from duckterm.core.agents_rules import RuleBlock, infer_scope, rule_id
        from duckterm.persistence.digests import _normalize

        directory = row.get("worktree_path") or row.get("cwd")
        if not directory:
            return
        base = Path(str(directory)).expanduser()
        if not (base / agents_rules.RULES_FILENAME).is_file():
            return
        root = str(Path(str(directory)))
        # normalized learning -> (representative text, session keys, runtimes, models)
        seen: dict[str, tuple[str, set[str], set[str], set[str]]] = {}
        for s in self.history.sessions():
            in_dir = any(
                str(s.get(field) or "").startswith(root)
                for field in ("cwd", "worktree_path", "repo_path")
            )
            if not in_dir:
                continue
            key = str(s["session_key"])
            for item in self.digests.items(key):
                if item["bucket"] != "user_learnings":
                    continue
                norm = _normalize(str(item["text"]))
                text, keys, runtimes, models = seen.setdefault(
                    norm, (str(item["text"]), set(), set(), set())
                )
                keys.add(key)
                if s.get("runtime"):
                    runtimes.add(str(s["runtime"]))
                if s.get("model"):
                    models.add(str(s["model"]))
        proposals = [
            RuleBlock(
                id=rule_id(text),
                text=text,
                scope=infer_scope(runtimes, models) if runtimes else "all",
                status="candidate",
                source="digest",
                evidence=len(keys),
                added=time.strftime("%Y-%m-%d"),
            )
            for text, keys, runtimes, models in seen.values()
            if len(keys) >= self._DIGEST_RULE_MIN_SESSIONS
        ]
        if not proposals:
            return
        # A hand-corrupted rules.json must not kill the digest loop — and must
        # not be "repaired" by overwriting it with an empty rule set.
        try:
            existing = agents_rules.load_rules(base)
        except (json.JSONDecodeError, TypeError):
            return
        from dataclasses import asdict

        before = [asdict(r) for r in existing]  # merge mutates evidence in place
        merged = agents_rules.merge_candidates(existing, proposals)
        if [asdict(r) for r in merged] != before:
            agents_rules.save_rules(base, merged)

    async def _session_digest(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        """The accumulated, validated digest archive for a session — the
        History tab's growing lists (latest summary rides the session row)."""
        await _write_json(writer, 200, {"items": self.digests.items(session_key)})

    def _progress_transcript(self, row: dict[str, Any]) -> list[dict[str, str]]:
        """Conversation records for the digest: the harness transcript when one
        exists, else the live terminal screen (generic agents)."""
        key = str(row.get("session_key") or "")
        cwd = row.get("worktree_path") or row.get("cwd")
        session_id = self.history.session_id_for(key)
        if cwd and session_id:
            runtime = _build_runtime(str(row.get("runtime") or "generic"), "")
            records = runtime.read_transcript(cwd=Path(str(cwd)), session_id=session_id)
            if records:
                return records
        sup = self.orchestrator.get(key)
        if sup is not None:
            screen = sup.screen_text(120)
            if screen:
                return [{"role": "terminal", "text": screen}]
        return []

    def _fleet_digest(self, row: dict[str, Any], question: str) -> str:
        key = str(row.get("session_key") or "")
        name = str(row.get("name") or row.get("source_app") or key)
        # A question that names a session gets a deeper look at that session.
        focus = name.lower() in question.lower()
        stats = self._transcript_stats_for(row)
        meta = [
            f"folder: {row.get('grp') or '-'}",
            f"state: {row.get('state') or '?'}",
            f"runtime: {row.get('runtime') or '?'}",
        ]
        if stats.get("model"):
            meta.append(f"model: {stats['model']}")
        if stats.get("context_tokens"):
            meta.append(f"context tokens: {stats['context_tokens']}")
        lines = [f"### {name} ({key})", " | ".join(meta)]
        if row.get("intention"):
            lines.append(f"goal: {row['intention']}")
        cps = self.history.checkpoints(key)
        if cps:
            lines.append(f"last checkpoint: {cps[0].get('summary') or cps[0].get('label')}")
        sup = self.orchestrator.get(key)
        if sup is not None:
            screen = sup.screen_text(120 if focus else 30)
            if screen:
                lines.append("recent terminal output:")
                lines.append(screen)
        return "\n".join(lines)

    async def _fleet_ask(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        """Answer a question about the currently running sessions: build a
        digest of each, ask the configured summarizer backend once."""
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        question = str(req.get("question") or "").strip()
        if not question:
            await _write_json(writer, 400, {"error": "question required"})
            return
        running = [
            r
            for r in self.history.sessions()
            if str(r.get("state") or "") not in self._FLEET_STATES_DONE
        ]
        if not running:
            await _write_json(
                writer, 200, {"answer": "No sessions are running right now.", "sessions": []}
            )
            return
        digests = "\n\n".join(self._fleet_digest(r, question) for r in running)
        history = [
            f"Q: {h.get('q')}\nA: {h.get('a')}"
            for h in (req.get("history") or [])[-2:]
            if isinstance(h, dict)
        ]
        prompt = (
            "You oversee a fleet of coding-agent sessions. Below is a digest of "
            "each currently running session (state, goal, recent terminal "
            "output). Answer the user's question about them, concise and "
            "concrete — name sessions by their name. If the digests don't hold "
            "the answer, say what to open instead of guessing.\n\n"
            + digests
            + ("\n\nEarlier exchanges:\n" + "\n".join(history) if history else "")
            + f"\n\nQuestion: {question}\nAnswer:"
        )
        result = await asyncio.to_thread(summarize, prompt)
        if not result.text:
            await _write_json(
                writer,
                502,
                {
                    "error": "no summarizer backend — install claude/codex/copilot "
                    "or set DUCKTERM_SUMMARIZER_CMD"
                },
            )
            return
        await _write_json(
            writer,
            200,
            {
                "answer": result.text,
                "sessions": [str(r.get("session_key")) for r in running],
                "backend": result.backend,
            },
        )

    async def _list_zsh_themes(self, writer: asyncio.StreamWriter) -> None:
        await _write_json(writer, 200, {"themes": zsh_themes.list_themes()})

    async def _list_harnesses(self, writer: asyncio.StreamWriter) -> None:
        """Registered installable harnesses, with manifest details re-read from
        disk (a suite that vanished from disk is reported, not hidden)."""
        out = []
        for row in self.history.harnesses():
            entry: dict[str, Any] = {"name": row["name"], "path": row["path"]}
            try:
                suite = suites.load(Path(str(row["path"])))
                entry["description"] = suite.description
                entry["has_manifest"] = suite.has_manifest
                entry["args_choices"] = suite.args_choices
                entry["uninstallable"] = suite.uninstall is not None
                entry["compatible"] = suite.compatible
            except (ValueError, OSError, json.JSONDecodeError) as e:
                entry["error"] = str(e)
            out.append(entry)
        await _write_json(writer, 200, {"harnesses": out})

    async def _register_harness(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        """Register a suite by its directory path. The directory must carry a
        duckterm-harness.json or an install.sh (the contract in suites.py)."""
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        raw = req.get("path")
        if not raw:
            await _write_json(writer, 400, {"error": "path required"})
            return
        path = Path(str(raw)).expanduser()
        if not path.is_dir():
            await _write_json(writer, 400, {"error": f"no such directory: {path}"})
            return
        try:
            suite = suites.load(path)
        except (ValueError, json.JSONDecodeError) as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        self.history.add_harness(suite.name, str(path), int(time.time() * 1000))
        await _write_json(
            writer,
            200,
            {"name": suite.name, "description": suite.description, "path": str(path)},
        )

    async def _install_harness(self, writer: asyncio.StreamWriter, name: str, body: bytes) -> None:
        """Run a registered suite's installer against a target directory:
        {dir, args?: [...]}. Output comes back verbatim so the user sees what
        the installer did (or why it failed)."""
        await self._run_suite(writer, name, body, action="install")

    async def _uninstall_harness(
        self, writer: asyncio.StreamWriter, name: str, body: bytes
    ) -> None:
        """Run a suite's declared uninstaller against a target directory.
        400 for suites whose manifest declares none."""
        await self._run_suite(writer, name, body, action="uninstall")

    async def _run_suite(
        self, writer: asyncio.StreamWriter, name: str, body: bytes, *, action: str
    ) -> None:
        row = next((h for h in self.history.harnesses() if h["name"] == name), None)
        if row is None:
            await _write_json(writer, 404, {"error": f"no harness {name!r} registered"})
            return
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        target = Path(str(req.get("dir") or "")).expanduser()
        if not target.is_dir():
            await _write_json(writer, 400, {"error": f"no such directory: {target}"})
            return
        args = [str(a) for a in req.get("args") or []]
        try:
            suite = suites.load(Path(str(row["path"])))
        except (ValueError, json.JSONDecodeError) as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        if action == "uninstall":
            if suite.uninstall is None:
                await _write_json(writer, 400, {"error": f"{name} declares no uninstall command"})
                return
            runner = suites.run_uninstall
        else:
            runner = suites.run_install
        ok, output = await asyncio.to_thread(runner, suite, target, args)
        await _write_json(
            writer,
            200 if ok else 502,
            {"ok": ok, "output": output, "harness": name, "dir": str(target)},
        )

    async def _harness_contents(self, writer: asyncio.StreamWriter, name: str) -> None:
        """What ships inside a registered suite — skills, sub-agents, hooks,
        guardrails, personas — each with the one-liner its own file declares."""
        row = next((h for h in self.history.harnesses() if h["name"] == name), None)
        if row is None:
            await _write_json(writer, 404, {"error": f"no harness {name!r} registered"})
            return
        try:
            suite = suites.load(Path(str(row["path"])))
        except (ValueError, json.JSONDecodeError) as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        await _write_json(
            writer,
            200,
            {"harness": name, "compatible": suite.compatible, "contents": suites.contents(suite)},
        )

    async def _deregister_harness(self, writer: asyncio.StreamWriter, name: str) -> None:
        removed = self.history.remove_harness(name)
        await _write_json(writer, 200 if removed else 404, {"removed": removed, "harness": name})

    async def _list_connectors(self, writer: asyncio.StreamWriter) -> None:
        # Credential/CLI probes can take seconds. Keep other dashboard requests
        # and terminal traffic responsive while they finish.
        statuses = await asyncio.to_thread(connectors.list_status)
        await _write_json(writer, 200, {"connectors": statuses})

    async def _enable_connector(self, writer: asyncio.StreamWriter, name: str, body: bytes) -> None:
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        token = str(req.get("token") or "").strip() or None
        secret = str(req.get("secret") or "").strip() or None
        try:
            result = connectors.enable(name, token, secret)
        except ValueError as e:
            await _write_json(writer, 404, {"error": str(e)})
            return
        except RuntimeError as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        await _write_json(writer, 200, result)

    async def _disable_connector(self, writer: asyncio.StreamWriter, name: str) -> None:
        try:
            result = connectors.disable(name)
        except ValueError as e:
            await _write_json(writer, 404, {"error": str(e)})
            return
        await _write_json(writer, 200, result)

    async def _backup(
        self, writer: asyncio.StreamWriter, headers: dict[str, str], method: str, body: bytes
    ) -> None:
        if not security.token_valid(headers, self.token):
            await _write_json(writer, 401, {"error": "owner credential required"})
            return
        try:
            if self._backup_jobs is None:
                self._backup_jobs = BackupJobs(paths.home() / "backup-state.json")
            jobs = self._backup_jobs
            if method == "GET":
                await _write_json(writer, 200, jobs.snapshot())
                return
            if len(body) > 8192:
                await _write_json(writer, 413, {"error": "request body too large"})
                return
            req = json.loads(body or b"{}")
            allowed = {"destination", "mode"} if method == "POST" else {"destination"}
            if not isinstance(req, dict) or set(req) - allowed:
                raise ValueError("Expected an object with destination and optional POST mode")
            if method == "POST" and jobs.task and not jobs.task.done():
                await _write_json(
                    writer, 409, {**jobs.snapshot(), "error": "A backup is already running"}
                )
                return
            mode = req.get("mode", "archive")
            if method == "POST":
                backup_sync.validate_mode(req.get("destination", jobs.state["destination"]), mode)
            if "destination" in req or method == "PUT":
                jobs.configure(req.get("destination"))
            result = jobs.start(mode) if method == "POST" else jobs.snapshot()
            await _write_json(writer, 202 if method == "POST" else 200, result)
        except (ValueError, UnicodeError) as exc:
            await _write_json(writer, 400, {"error": str(exc)})
        except OSError as exc:
            await _write_json(writer, 500, {"error": str(exc)})

    async def _folder_broadcast(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str],
        folder: str,
        method: str,
        body: bytes,
    ) -> None:
        if not security.token_valid(headers, self.token):
            await _write_json(writer, 401, {"error": "owner credential required"})
            return
        try:
            folder = urllib.parse.unquote(folder)
            if folder not in self.history.folders():
                raise APIError(404, "folder not found")
            if method == "GET":
                result = {
                    "folder": folder,
                    "targets": self.history.session_api.broadcast_targets(folder),
                }
            else:
                if len(body) > MAX_BODY_BYTES:
                    raise APIError(413, "request body too large")
                req = json.loads(body or b"{}")
                if not isinstance(req, dict):
                    raise APIError(400, "expected a JSON object")
                result = self.history.session_api.broadcast(folder, req)
            await _write_json(writer, 200 if method == "GET" else 202, result)
        except (ValueError, UnicodeDecodeError):
            await _write_json(writer, 400, {"error": "invalid JSON"})
        except APIError as exc:
            await _write_json(writer, exc.status, {"error": str(exc)})

    async def _list_folders(self, writer: asyncio.StreamWriter) -> None:
        await _write_json(writer, 200, {"folders": self.history.folders()})

    async def _create_folder(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        try:
            name = str(json.loads(body or b"{}").get("name", "")).strip()
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        if not name:
            await _write_json(writer, 400, {"error": "name required"})
            return
        self.history.create_folder(name, now=int(time.time() * 1000))
        await _write_json(writer, 200, {"created": name})

    async def _delete_folder(self, writer: asyncio.StreamWriter, name: str) -> None:
        self.history.delete_folder(urllib.parse.unquote(name))
        await _write_json(writer, 200, {"deleted": urllib.parse.unquote(name)})

    async def _move_folder(self, writer: asyncio.StreamWriter, name: str, body: bytes) -> None:
        """Re-parent and/or rename a folder: {parent: "a/b"} nests it there,
        {parent: ""} moves it to the top level, {name: "x"} renames the leaf;
        an omitted field keeps its current value. Subfolders and grouped
        sessions follow (a folder is a path prefix, so both are a prefix
        rename)."""
        old = urllib.parse.unquote(name)
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        if "parent" in req:
            parent = str(req.get("parent") or "").strip().strip("/")
        else:
            parent = old.rsplit("/", 1)[0] if "/" in old else ""
        leaf = str(req.get("name") or "").strip().strip("/") or old.rsplit("/", 1)[-1]
        if "/" in leaf:
            await _write_json(writer, 400, {"error": "name can't contain '/'"})
            return
        new = f"{parent}/{leaf}" if parent else leaf
        if new == old:
            await _write_json(writer, 200, {"moved": old, "to": new})
            return
        try:
            moved = self.history.move_folder(old, new)
        except ValueError as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        if not moved:
            await _write_json(writer, 404, {"error": f"no folder {old!r}"})
            return
        await _write_json(writer, 200, {"moved": old, "to": new})

    async def _tree(self, writer: asyncio.StreamWriter) -> None:
        await _write_json(writer, 200, {"nodes": self.history.fork_tree()})

    async def _terminals(self, writer: asyncio.StreamWriter) -> None:
        await _write_json(writer, 200, {"terminals": available_terminals()})

    async def _get_session(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        # Only a bare key — sub-paths like /diff, /output are their own routes.
        if "/" in session_key:
            await _write_response(writer, 404, "not found")
            return
        row = self.history.session(session_key)
        if row is None:
            await _write_json(writer, 404, {"error": "no such session"})
            return
        await _write_json(writer, 200, row)

    async def _browse(self, writer: asyncio.StreamWriter, seg: str) -> None:
        # seg is the part of the path after "/browse", e.g. "?path=/Users/x".
        query = urllib.parse.urlparse("/browse" + seg).query
        path = urllib.parse.parse_qs(query).get("path", [None])[0]
        await _write_json(writer, 200, browse.listing(path))

    @staticmethod
    def _agents_md_dir_allowed(directory: str) -> bool:
        """Confine AGENTS.md reads/writes the way /browse confines listings:
        the home tree (where real projects live) plus the system tempdir
        (worktrees, scratch projects, tests). Without this the endpoint was an
        arbitrary-path file write for anything reaching the dashboard origin."""
        p = Path(directory).expanduser().resolve()
        home = Path.home().resolve()
        # gettempdir() itself, NOT its parent: on Linux that parent is "/",
        # which made this check a no-op (and /etc writable). /tmp is listed
        # explicitly because on macOS gettempdir() is /var/folders/… while
        # scratch projects and tests live under /tmp (-> /private/tmp).
        scratch = (Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve())
        return p.is_relative_to(home) or any(p.is_relative_to(t) for t in scratch)

    # ── plain-file editor (e.g. .env secrets an agent asks you to fill in
    #    without pasting them into the conversation) ──

    _FILE_MAX_BYTES = 1_000_000  # editor guard: this is for configs, not blobs

    async def _read_file(self, writer: asyncio.StreamWriter, seg: str) -> None:
        """Read a text file (?path=…) for the in-dashboard editor. Same path
        confinement as AGENTS.md: home tree + tempdir only."""
        query = urllib.parse.urlparse("/file" + seg).query
        raw = urllib.parse.parse_qs(query).get("path", [None])[0]
        if not raw:
            await _write_json(writer, 400, {"error": "path is required"})
            return
        if not self._agents_md_dir_allowed(raw):
            await _write_json(writer, 403, {"error": "path outside the allowed roots"})
            return
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            await _write_json(writer, 400, {"error": "path is a directory"})
            return
        text = ""
        if path.exists():
            if path.stat().st_size > self._FILE_MAX_BYTES:
                await _write_json(writer, 400, {"error": "file too large for the editor"})
                return
            text = path.read_text(errors="replace")
        await _write_json(writer, 200, {"path": str(path), "text": text, "exists": path.exists()})

    _PASTE_MAX_BYTES = 10_000_000  # clipboard screenshots, not videos

    async def _paste_image(
        self, writer: asyncio.StreamWriter, headers: dict[str, str], body: bytes
    ) -> None:
        """Save a pasted clipboard image and return its file path — the
        terminal then types that path, which both claude and codex read as an
        image attachment (the iTerm paste-an-image experience). Files land
        under DUCKTERM_HOME/pastes; nothing user-controlled shapes the name."""
        if not body:
            await _write_json(writer, 400, {"error": "empty image"})
            return
        if len(body) > self._PASTE_MAX_BYTES:
            await _write_json(writer, 413, {"error": "image too large (10MB cap)"})
            return
        ctype = headers.get("content-type", "image/png")
        ext = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
        }.get(ctype, "png")
        from duckterm.helpers import paths as _paths

        directory = _paths.home() / "pastes"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"paste-{uuid.uuid4().hex[:12]}.{ext}"
        path.write_bytes(body)
        await _write_json(writer, 200, {"path": str(path)})

    async def _write_file(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        """Write a text file for the in-dashboard editor. Secrets typed here go
        straight to disk — never through an agent's conversation. chmod 600 on
        create for dotfiles, since the common case is credentials."""
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        raw, text = str(req.get("path") or ""), req.get("text")
        if not raw or not isinstance(text, str):
            await _write_json(writer, 400, {"error": "path and text are required"})
            return
        if len(text.encode()) > self._FILE_MAX_BYTES:
            await _write_json(writer, 400, {"error": "file too large for the editor"})
            return
        if not self._agents_md_dir_allowed(raw):
            await _write_json(writer, 403, {"error": "path outside the allowed roots"})
            return
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            await _write_json(writer, 400, {"error": "path is a directory"})
            return
        fresh = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if fresh and path.name.startswith("."):
            path.chmod(0o600)
        await _write_json(writer, 200, {"path": str(path), "written": True})

    async def _read_agents_md(self, writer: asyncio.StreamWriter, seg: str) -> None:
        """Read the typed rule set for a folder (?dir=…). `rules` come from
        .duckterm-rules.json (the source of truth); `text` is the current
        AGENTS.md — rendered output when managed, the hand-written file when a
        folder predates the typed format (managed=false, so the editor can
        offer an import instead of clobbering it)."""
        from dataclasses import asdict

        from duckterm.core import agents_rules

        query = urllib.parse.urlparse("/agents-md" + seg).query
        directory = urllib.parse.parse_qs(query).get("dir", [None])[0]
        if not directory:
            await _write_json(writer, 400, {"error": "dir required"})
            return
        if not self._agents_md_dir_allowed(directory):
            await _write_json(writer, 400, {"error": "dir outside the home tree"})
            return
        base = Path(directory).expanduser().resolve()
        path = base / "AGENTS.md"
        if not self._agents_md_dir_allowed(str(path)):
            await _write_json(writer, 400, {"error": "file outside the allowed roots"})
            return
        rules = agents_rules.load_rules(base)
        text = path.read_text() if path.is_file() else ""
        await _write_json(
            writer,
            200,
            {
                "dir": directory,
                "rules": [asdict(r) for r in rules],
                "text": text,
                "exists": path.is_file(),
                "managed": (base / agents_rules.RULES_FILENAME).is_file(),
            },
        )

    async def _suggest_agents_md(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        """The observation loop: {dir} -> proposed AGENTS.md rules distilled
        from the corrections users gave agents working in that folder
        (annotations + mid-session follow-up prompts). Proposals go back to the
        editor for review — this never writes the file."""
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        directory = req.get("dir")
        if not directory:
            await _write_json(writer, 400, {"error": "dir required"})
            return
        path = (Path(str(directory)).expanduser() / "AGENTS.md").resolve()
        if not self._agents_md_dir_allowed(str(path)):
            await _write_json(writer, 400, {"error": "file outside the allowed roots"})
            return
        from dataclasses import asdict

        from duckterm.core import agents_rules

        corrections = self._corrections_for_dir(str(directory))
        existing = agents_rules.load_rules(path.parent)
        proposed = await asyncio.to_thread(suggest_rules, corrections, existing)
        # Rejected tombstones and already-active rules block re-proposal.
        taken = {r.id for r in existing}
        fresh = [r for r in proposed if r.id not in taken]
        await _write_json(
            writer,
            200,
            {"suggestions": [asdict(r) for r in fresh], "corrections_seen": len(corrections)},
        )

    def _corrections_for_dir(self, directory: str) -> "list[Correction]":
        """Every correction signal from sessions that worked in `directory`:
        annotation notes (span + pushback) and follow-up prompts (every
        UserPromptSubmit after a session's first — the first is the task,
        later ones are steering). Bounded so the LLM prompt stays small."""
        root = str(Path(directory))
        out: list[Correction] = []
        for row in self.history.sessions():
            in_dir = any(
                str(row.get(field) or "").startswith(root)
                for field in ("cwd", "worktree_path", "repo_path")
            )
            if not in_dir:
                continue
            key = str(row["session_key"])
            runtime = str(row.get("runtime") or "")
            model = str(row.get("model") or "")
            for ann in self.history.annotations(key):
                out.append(
                    Correction("annotation", f"\"{ann['quote']}\" — {ann['note']}", runtime, model)
                )
            prompts = [
                str(e.get("prompt"))
                for e in self.history.events_for(key)
                if e.get("event_type") == events.USER_PROMPT_SUBMIT and e.get("prompt")
            ]
            out.extend(Correction("follow-up", p, runtime, model) for p in prompts[1:])
        return out[-80:]  # newest-biased cap; enough signal, bounded prompt

    async def _write_agents_md(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        """Save a folder's rule set: {dir, rules: [...]} writes
        .duckterm-rules.json AND renders AGENTS.md from it (the only writer, so
        the two never drift). Legacy {dir, text} still writes a plain AGENTS.md
        for folders that never adopted the typed format."""
        from duckterm.core import agents_rules

        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        directory = req.get("dir")
        text = req.get("text")
        rules_raw = req.get("rules")
        if not directory or (not isinstance(text, str) and not isinstance(rules_raw, list)):
            await _write_json(writer, 400, {"error": "dir and rules (or text) are required"})
            return
        if not self._agents_md_dir_allowed(str(directory)):
            await _write_json(writer, 400, {"error": "dir outside the home tree"})
            return
        base = Path(directory).expanduser().resolve()
        if not base.is_dir():
            await _write_json(writer, 400, {"error": f"no such directory: {directory}"})
            return
        path = (base / "AGENTS.md").resolve()
        if not self._agents_md_dir_allowed(str(path)):
            await _write_json(writer, 400, {"error": "file outside the allowed roots"})
            return
        if isinstance(rules_raw, list):
            known = set(agents_rules.RuleBlock.__dataclass_fields__)
            rules: list[agents_rules.RuleBlock] = []
            for entry in rules_raw:
                if not isinstance(entry, dict) or not entry.get("id") or not entry.get("text"):
                    await _write_json(writer, 400, {"error": "each rule needs id and text"})
                    return
                if entry.get("status", "candidate") not in agents_rules.STATUSES:
                    await _write_json(writer, 400, {"error": f"bad status on {entry['id']}"})
                    return
                rules.append(
                    agents_rules.RuleBlock(**{k: v for k, v in entry.items() if k in known})
                )
            agents_rules.save_rules(base, rules)
        else:
            path.write_text(text)
        await _write_json(writer, 200, {"dir": directory, "written": True})

    async def _branches(self, writer: asyncio.StreamWriter, seg: str) -> None:
        """Branches in the repo at ?path=, for the 'base off' picker. Fetches
        first so freshly-pushed remote branches appear."""
        query = urllib.parse.urlparse("/branches" + seg).query
        path = urllib.parse.parse_qs(query).get("path", [None])[0]
        if not path:
            await _write_json(writer, 400, {"error": "path required"})
            return
        try:
            names = await asyncio.to_thread(self.orchestrator.worktrees.branches, Path(path))
        except GitError as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        await _write_json(writer, 200, {"branches": names})

    def _can_answer(self, session_key: str) -> bool:
        # Answerable from the dashboard only if we own the session's PTY and can
        # inject keystrokes. Blocking approvals don't need this — the hook polls.
        return self.orchestrator.get(session_key) is not None

    async def _register_approval(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        """A blocking pre-exec hook registers a permission request and gets an id
        to poll. The dashboard answers it via /decide; the hook reads the answer
        from /decision and returns it to the agent — so the dashboard is the
        approval authority (no keystroke injection)."""
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        key = req.get("session_key") or req.get("session_id")
        if not key:
            await _write_json(writer, 400, {"error": "session_key required"})
            return
        # Launched-only, same rule as /events ingest: a blocking hook from a
        # session Duckterm didn't start gets no approval id, so it falls through
        # to the agent's own terminal prompt instead of parking an approval on a
        # session the dashboard doesn't show.
        if not req.get("session_key") and self.history.session(str(key)) is None:
            await _write_json(writer, 200, {"id": None})
            return
        # AskUserQuestion is the agent asking the human a multiple-choice question,
        # not a tool-permission gate. The dashboard can't answer it with
        # approve/deny, so don't register it — the agent prompts in its terminal.
        # (Defends against pre-update hooks that still POST it; current hooks skip
        # it client-side.)
        if req.get("tool_name") == "AskUserQuestion":
            await _write_json(writer, 200, {"id": None})
            return
        approval = self.approvals.register(
            str(key),
            str(req.get("tool_name") or "unknown"),
            req.get("tool_input") or {},
            int(time.time() * 1000),
            blocking=True,
        )
        await _write_json(writer, 200, {"id": approval.id})

    async def _approval_decision(self, writer: asyncio.StreamWriter, approval_id: str) -> None:
        """The blocking hook polls this for the user's decision. `pending` while
        unanswered; `approve`/`deny` once decided; `gone` if the request was
        cleared (the hook should then fall through to the agent's own prompt)."""
        a = self.approvals.get(approval_id)
        if a is None:
            await _write_json(writer, 200, {"status": "gone"})
            return
        if a.decided is None:
            await _write_json(writer, 200, {"status": "pending"})
            return
        # Decided: report it, then forget so the registry doesn't accumulate.
        decision = a.decided
        self.approvals.forget(approval_id)
        await _write_json(writer, 200, {"status": decision})

    async def _list_approvals(self, writer: asyncio.StreamWriter) -> None:
        # Zombie sweep by wall clock — event-driven cleanup starves during
        # long tool runs (see ApprovalRegistry.expire_stale_blocking).
        self.approvals.expire_stale_blocking(int(time.time() * 1000), _BLOCKING_POLL_MS)
        pending = [
            {
                "id": a.id,
                "session_key": a.session_key,
                "tool_name": a.tool_name,
                "detail": a.detail,
                "created_at": a.created_at,
                # A blocking request is always answerable here (the dashboard is
                # the authority). An observe-only row is answerable only if we own
                # the PTY or launched the tab; otherwise you answer in its terminal.
                "reachable": a.blocking or self._can_answer(a.session_key),
            }
            for a in self.approvals.pending()
        ]
        await _write_json(writer, 200, {"approvals": pending})

    async def _decide_approval(
        self, writer: asyncio.StreamWriter, approval_id: str, body: bytes
    ) -> None:
        decision = json.loads(body or b"{}").get("decision")
        if decision not in ("approve", "deny"):
            await _write_json(writer, 400, {"error": "decision must be approve or deny"})
            return
        a = self.approvals.get(approval_id)
        if a is None:
            await _write_json(writer, 409, {"decided": False, "decision": decision})
            return
        # Record the decision. A blocking hook polling /decision picks it up and
        # returns it to the agent.
        decided = self.approvals.set_decision(approval_id, decision)
        status = 200 if decided else 409
        await _write_json(writer, status, {"decided": decided, "decision": decision})

    def _worktree_of(self, session_key: str) -> str | None:
        row = self.history.session(session_key)
        if row is None:
            return None
        wt = row.get("worktree_path")
        return str(wt) if wt else None

    async def _checkpoint(
        self, writer: asyncio.StreamWriter, session_key: str, body: bytes
    ) -> None:
        row = self.history.session(session_key)
        if row is None:
            # The row is gone — almost always because the session was deleted
            # while its (watched) row still lingered in a stale dashboard tab.
            msg = f"session {session_key} no longer exists (it may have been deleted)"
            await _write_json(writer, 404, {"error": msg})
            return
        label = json.loads(body or b"{}").get("label", "checkpoint")
        cwd = Path(str(row.get("worktree_path") or row.get("cwd") or "."))
        # Summarize the delta since the most recent checkpoint (0 if first).
        prior = self.history.checkpoints(session_key)
        since_ms = int(prior[0]["created_at"]) if prior else 0
        # Read the agent's own conversation (including its responses) from its
        # native transcript, so the checkpoint captures what the agent said and
        # did — not just the human prompts and tool calls in our event store.
        transcript = self._read_transcript(session_key, row)
        cp = await asyncio.to_thread(
            build_checkpoint,
            session_key=session_key,
            label=label,
            cwd=cwd,
            # The whole session, not just the last 200 events — a checkpoint is
            # a complete record and must capture every prompt.
            events=self.history.events_for(session_key, limit=100_000),
            transcript=transcript,
            intention=str(row.get("intention") or ""),
            now_ms=int(time.time() * 1000),
            since_ms=since_ms,
        )
        # Row FIRST (the source of truth), markdown SECOND (a derived artifact).
        # A crash between them leaves markdown_path NULL — never an orphan file
        # with no row. The markdown lives under DUCKTERM_HOME, not the worktree,
        # so deleting the session's worktree can't destroy its checkpoint log.
        self.history.add_checkpoint(
            checkpoint_id=cp.id,
            session_key=cp.session_key,
            label=cp.label,
            summary=cp.summary,
            record=cp.record,
            markdown_path=None,
            created_at=cp.created_at,
        )
        rel = await asyncio.to_thread(write_markdown, cp.session_key, cp.created_at, cp.markdown)
        if rel is not None:
            self.history.set_checkpoint_markdown(cp.id, rel)
        await _write_json(writer, 200, {"id": cp.id, "label": cp.label, "summary": cp.summary})

    def _read_transcript(self, session_key: str, row: dict[str, Any]) -> list[dict[str, str]]:
        """The agent's own conversation for a session (role/text incl. its
        responses), read from its native transcript. Empty when we can't (no
        runtime/session_id, or the agent keeps no readable transcript)."""
        session_id = self.history.session_id_for(session_key)
        if not session_id:
            return []
        runtime = _build_runtime(row.get("runtime"), "")
        cwd = Path(str(row.get("worktree_path") or row.get("cwd") or "."))
        try:
            return runtime.read_transcript(cwd=cwd, session_id=session_id)
        except OSError:
            return []

    async def _list_checkpoints(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        await _write_json(writer, 200, {"checkpoints": self.history.checkpoints(session_key)})

    async def _spotlight(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        row = self.history.session(session_key)
        if row is None or not row.get("worktree_path") or not row.get("repo_path"):
            await _write_json(writer, 400, {"error": "session has no worktree/repo"})
            return
        try:
            files = spotlight_to_main(
                repo=Path(str(row["repo_path"])), worktree=Path(str(row["worktree_path"]))
            )
        except GitError as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        await _write_json(writer, 200, {"synced_files": files})

    async def _diff(self, writer: asyncio.StreamWriter, session_key: str) -> None:
        worktree = self._worktree_of(session_key)
        if not worktree:
            await _write_json(writer, 200, {"diff": ""})
            return
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", worktree, "diff", "HEAD"],
            capture_output=True,
            text=True,
        )
        # A nonzero exit means the diff failed (bad repo, detached state). Don't
        # report it as an empty diff — that reads as "no changes" in the UI.
        if result.returncode != 0:
            await _write_json(writer, 500, {"error": result.stderr.strip() or "git diff failed"})
            return
        await _write_json(writer, 200, {"diff": result.stdout})

    async def _input(self, writer: asyncio.StreamWriter, session_key: str, body: bytes) -> None:
        supervisor = self.orchestrator.get(session_key)
        if supervisor is None:
            await _write_json(writer, 404, {"error": "no live session (not launched by Duckterm)"})
            return
        text = json.loads(body or b"{}").get("text", "")
        wrote = supervisor.write_input(text)
        await _write_json(writer, 200 if wrote else 409, {"written": wrote})

    async def _output(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, session_key: str
    ) -> None:
        supervisor = self.orchestrator.get(session_key)
        if supervisor is None:
            await _write_json(writer, 404, {"error": "no live session to stream"})
            return
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n\r\n"
        )
        await writer.drain()
        feed = supervisor.subscribe_output()
        disconnect = asyncio.ensure_future(reader.read())
        try:
            while True:
                nxt = asyncio.ensure_future(feed.__anext__())
                done, _ = await asyncio.wait(
                    {nxt, disconnect},
                    timeout=KEEPALIVE_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect in done:
                    nxt.cancel()
                    break
                if nxt not in done:
                    nxt.cancel()
                    writer.write(b": keepalive\r\n\r\n")
                    await writer.drain()
                    continue
                writer.write(f"data: {json.dumps({'line': nxt.result()})}\n\n".encode())
                await writer.drain()
        finally:
            disconnect.cancel()
            await feed.aclose()

    async def _terminal(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: dict[str, str],
        session_key: str,
    ) -> None:
        """WebSocket terminal attach for a launched session. Streams raw PTY
        bytes to the browser as binary frames (xterm.js renders them) and reads
        client frames back: binary = keystrokes to the agent's stdin, text =
        a JSON control message {"resize": {"cols", "rows"}}.

        Only works for a session Duckterm launched (it owns the PTY/tmux).
        Additive — leaves /ws (events) and /output (SSE line view) untouched."""
        supervisor = self.orchestrator.get(session_key)
        # A stopped session's supervisor stays registered but its PTY is gone —
        # attaching to it would hang a silent, never-ending connection. Refuse
        # instead, so a reconnecting client keeps retrying and lands on the NEW
        # supervisor the moment a Resume replaces the dead one.
        if supervisor is None or not supervisor.running:
            await _write_json(writer, 404, {"error": "no live session to attach"})
            return
        key = headers.get("sec-websocket-key")
        if not key:
            await _write_response(writer, 400, "expected a WebSocket upgrade")
            return
        writer.write(handshake_response(key))
        await writer.drain()

        feed = supervisor.subscribe_bytes()
        # Keep ONE pending future for each side across loop iterations. Never
        # cancel the output future mid-flight: cancelling an in-flight
        # `feed.__anext__()` corrupts the async generator, so the next call
        # raises StopAsyncIteration and the connection dies the instant the user
        # types. We re-create a side's future only after it actually completes.
        outgoing = asyncio.ensure_future(feed.__anext__())
        incoming = asyncio.ensure_future(read_frame(reader))
        try:
            while True:
                done, _ = await asyncio.wait(
                    {outgoing, incoming},
                    timeout=KEEPALIVE_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:  # keepalive tick: nothing on either side
                    writer.write(ping_frame())
                    await writer.drain()
                    continue
                if incoming in done:
                    frame = incoming.result()
                    if frame is None or frame[0] == 0x8:  # EOF or client close
                        break
                    self._handle_terminal_frame(supervisor, frame)
                    incoming = asyncio.ensure_future(read_frame(reader))
                if outgoing in done:
                    writer.write(encode_binary_frame(outgoing.result()))
                    await writer.drain()
                    outgoing = asyncio.ensure_future(feed.__anext__())
        except (StopAsyncIteration, OSError, ValueError, asyncio.IncompleteReadError):
            pass
        finally:
            outgoing.cancel()
            incoming.cancel()
            # Await the cancelled output future before aclose(): you can't close
            # an async generator while a __anext__() on it is still running
            # ("asynchronous generator is already running").
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await outgoing
            await feed.aclose()
            with contextlib.suppress(OSError):
                writer.write(close_frame())
                await writer.drain()

    @staticmethod
    def _handle_terminal_frame(supervisor: Any, frame: tuple[int, bytes]) -> None:
        opcode, payload = frame
        if opcode == 0x2:  # binary: raw keystrokes
            # queue_bytes, not write_bytes: tmux send-keys is a ~10ms
            # subprocess, and running it inline here stalled the event loop
            # (and every other session's stream) on each keypress.
            supervisor.queue_bytes(payload)
        elif opcode == 0x1:  # text: a JSON control message
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                return
            resize = msg.get("resize")
            if isinstance(resize, dict):
                cols, rows = resize.get("cols"), resize.get("rows")
                if isinstance(cols, int) and isinstance(rows, int):
                    supervisor.resize(cols, rows)

    async def _compare(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        try:
            req: Any = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await _write_json(writer, 400, {"error": "invalid JSON"})
            return
        repo_path = req.get("repo_path")
        prompt = req.get("prompt", "")
        variants = req.get("variants")
        if not repo_path or not isinstance(variants, list) or not variants:
            await _write_json(
                writer, 400, {"error": "repo_path and a non-empty variants list are required"}
            )
            return
        group = req.get("group") or f"cmp-{int(time.time() * 1000)}"
        keys = []
        try:
            for i, v in enumerate(variants):
                key = await self.orchestrator.launch(
                    runtime=_build_runtime(v.get("runtime", "generic"), v["command"]),
                    repo_path=repo_path,
                    branch=f"{group}/{v.get('runtime', 'generic')}-{i}",
                    prompt=prompt,
                    compare_group=group,
                )
                keys.append(key)
        except (GitError, ValueError, KeyError) as e:
            await _write_json(writer, 400, {"error": str(e)})
            return
        await _write_json(writer, 200, {"group": group, "session_keys": keys})

    async def _create_snapshot(self, writer: asyncio.StreamWriter) -> None:
        snapshot_id = self.snapshots.create(now_ms=int(time.time() * 1000))
        await _write_json(writer, 200, {"id": snapshot_id})

    async def _list_snapshots(self, writer: asyncio.StreamWriter) -> None:
        await _write_json(writer, 200, {"snapshots": self.snapshots.list()})

    async def _get_snapshot(self, writer: asyncio.StreamWriter, snapshot_id: str) -> None:
        manifest = self.snapshots.get(snapshot_id)
        if manifest is None:
            await _write_json(writer, 404, {"error": f"no snapshot {snapshot_id}"})
            return
        await _write_json(writer, 200, manifest)

    async def _restore(self, writer: asyncio.StreamWriter, route: str) -> None:
        # route is "<snapshot_id>/sessions/<session_key>"
        parts = route.split("/sessions/")
        if len(parts) != 2:
            await _write_json(writer, 400, {"error": "bad restore path"})
            return
        snapshot_id, session_key = parts
        manifest = self.snapshots.get(snapshot_id)
        if manifest is None:
            await _write_json(writer, 404, {"error": f"no snapshot {snapshot_id}"})
            return
        session = next((s for s in manifest["sessions"] if s["session_key"] == session_key), None)
        if session is None:
            await _write_json(writer, 404, {"error": f"no session {session_key} in snapshot"})
            return
        cwd = str(session.get("worktree_path") or session.get("cwd") or ".")
        # The worktree/dir a snapshot points at may be gone (session deleted,
        # `git worktree prune`, home wiped) — launching into a non-existent
        # directory silently lands the agent in $HOME with the branch missing.
        # Refuse with a clear reason instead. Checked BEFORE resolving the resume
        # id (which may read a transcript) so a dead worktree short-circuits.
        if not Path(cwd).is_dir():
            await _write_json(
                writer,
                409,
                {
                    "error": "the session's working directory no longer exists — "
                    "its worktree was likely removed",
                    "cwd": cwd,
                },
            )
            return
        # `--resume` needs the harness's OWN conversation id, not Duckterm's
        # session_key. Resolve it (same as conversation-fork); if there's nothing
        # resumable, restore_command_for falls back to a fresh launch.
        argv = restore_command_for(self._restore_session_with_resume_id(session))
        # Restore under the snapshot's original session_key so the relaunched
        # agent re-attaches to its row (its hooks report under this key via the
        # env var) instead of spawning an untracked session. Without the env +
        # heartbeat + SessionStart, the restored agent ran but never showed up.
        key = str(session["session_key"])
        restore_runtime = _build_runtime(session.get("runtime"), shlex.join(argv))
        argv = restore_runtime.launch_command(
            cwd=Path(cwd),
            session_key=key,
            initial_prompt=self._collaboration_prompt(restore_runtime.name, key),
        )
        self.bus.publish(
            {
                "event_type": events.SESSION_START,
                "session_key": key,
                "name": session.get("name"),
                "runtime": session.get("runtime"),
                "cwd": session.get("cwd"),
                "worktree_path": session.get("worktree_path"),
                "branch": session.get("branch"),
                "source_app": session.get("source_app"),
                "launched": True,
            }
        )
        spawned = open_in_terminal(
            cwd,
            argv,
            env=self._session_env(key),
            heartbeat=(instance.heartbeat_url(), key),
            title=session.get("name") or session.get("source_app"),
        )
        if spawned:
            self.history.mark_heartbeat(key)
        else:
            self.bus.publish({"event_type": events.SESSION_END, "session_key": key})
        await _write_json(writer, 200, {"restored": spawned, "command": shlex.join(argv)})

    async def _stream(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Connection: keep-alive\r\n" + f"{SELF_PROBE_HEADER}: 1\r\n\r\n".encode()
        )
        await writer.drain()
        _write_sse(writer, {"type": "init", "events": self._init_events()})
        await writer.drain()

        subscription = self.bus.subscribe()
        # An EOF on the client reader means they disconnected; race it against each
        # event wait so we stop promptly instead of blocking until the next keepalive.
        disconnect = asyncio.ensure_future(reader.read())
        try:
            while True:
                nxt = asyncio.ensure_future(subscription.next())
                done, _ = await asyncio.wait(
                    {nxt, disconnect},
                    timeout=KEEPALIVE_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect in done:
                    nxt.cancel()
                    break
                if nxt not in done:
                    nxt.cancel()
                    writer.write(b": keepalive\r\n\r\n")
                    await writer.drain()
                    continue
                _write_sse(writer, nxt.result())
                await writer.drain()
        finally:
            disconnect.cancel()
            subscription.close()

    async def _websocket(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: dict[str, str],
    ) -> None:
        """Bidirectional event stream over WebSocket, a sibling to /stream. Sends
        an init frame, then one text frame per event; closes on client close."""
        key = headers.get("sec-websocket-key")
        if not key:
            await _write_response(writer, 400, "expected a WebSocket upgrade")
            return
        writer.write(handshake_response(key))
        await writer.drain()
        writer.write(encode_text_frame(json.dumps({"type": "init", "events": self._init_events()})))
        await writer.drain()

        subscription = self.bus.subscribe()
        incoming = asyncio.ensure_future(read_frame_opcode(reader))
        try:
            while True:
                nxt = asyncio.ensure_future(subscription.next())
                done, _ = await asyncio.wait(
                    {nxt, incoming}, timeout=KEEPALIVE_SECONDS, return_when=asyncio.FIRST_COMPLETED
                )
                if incoming in done:
                    opcode = incoming.result()
                    nxt.cancel()
                    if opcode in (None, 0x8):  # EOF or close frame
                        break
                    incoming = asyncio.ensure_future(read_frame_opcode(reader))
                    continue
                if nxt not in done:
                    nxt.cancel()
                    continue  # keepalive tick; nothing to send
                writer.write(encode_text_frame(json.dumps(nxt.result())))
                await writer.drain()
        except (ValueError, asyncio.IncompleteReadError):
            pass
        finally:
            nxt.cancel()
            incoming.cancel()
            subscription.close()
            with contextlib.suppress(OSError):
                writer.write(close_frame())
                await writer.drain()

    async def serve(
        self,
        host: str,
        port: int,
        on_listening: Callable[[str, int], None] | None = None,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Duckterm must bind a loopback host (127.0.0.1, localhost, or ::1)")
        # Refuse to start if another live server already owns this DUCKTERM_HOME.
        # Two servers on one home share a DB and (via a shared tmux socket)
        # adopt each other's panes — a second instance can archive or kill the
        # first's live agents. The pidfile turns that silent corruption into a
        # clear error. (Per-instance home/socket/port normally prevent overlap;
        # this guards the case where they were misconfigured to collide.)
        lock = _acquire_home_lock()
        try:
            adopted = await self.orchestrator.reconcile()
            if adopted:
                print(f"re-adopted {len(adopted)} tmux session(s): {', '.join(adopted)}")
            # Backfill progress digests: sessions whose turns all happened
            # before this server started would otherwise stay digest-less
            # until their NEXT turn end (Stop events don't replay).
            for row in self.history.sessions():
                at_rest = row.get("state") in AT_REST_STATES
                if not at_rest and not row.get("progress"):
                    self._maybe_refresh_progress(str(row["session_key"]))
            server = await asyncio.start_server(self.handle, host, port)
            actual_port = server.sockets[0].getsockname()[1]
            self.history.session_api.set_url(f"http://127.0.0.1:{actual_port}")
            if on_listening is not None:
                on_listening(host, port)
            sweeper = asyncio.create_task(self._sweep_dead_loop())
            async with server:
                try:
                    await server.serve_forever()
                finally:
                    sweeper.cancel()
        finally:
            _release_home_lock(lock)

    async def _sweep_dead_loop(self) -> None:
        """Auto-archive sessions whose terminal is gone. Launched tabs ping every
        20s; we archive after 60s of silence. Watched sessions (no heartbeat) are
        archived when their recorded agent pid is no longer alive. Archived keeps
        everything (resumable) — it's not delete."""
        ticks = 0
        while True:
            await asyncio.sleep(20)
            ticks += 1
            if ticks % 3 == 0 and os.environ.get("DUCKTERM_ORACLE") != "off":
                await self._oracle_tick()
            now = int(time.time() * 1000)
            for key in self.history.sweep_dead(now, stale_after_ms=60_000):
                self._archive_swept(key)
            for w in self.history.live_watched():
                if not _pid_alive(int(w["agent_pid"])):
                    self._archive_swept(str(w["session_key"]))

    async def _oracle_tick(self) -> None:
        """Paste an inbox reminder into idle agents whose mail would otherwise
        wait until the owner happens to look. Gates live in core/oracle.py."""
        now = int(time.time() * 1000)
        for row in self.history.sessions():
            key = str(row["session_key"])
            sup = self.orchestrator.get(key)
            if row.get("state") != "idle" or sup is None or not sup.running:
                continue
            try:
                mail = self.history.session_api.open_mail(key)
            except APIError:
                continue
            if not mail:
                continue
            screen = await asyncio.to_thread(sup.visible_screen)
            picked = oracle.should_nudge(
                state="idle",
                turn_ended_ms=self.history.last_event_ts(key, events.STOP),
                observed_since_ms=sup.observed_since_ms,
                last_owner_input_ms=sup.last_owner_input_ms,
                prompt_empty=sup.runtime.prompt_is_empty(screen),
                mail=mail,
                previous=self._oracle_nudges.get(key),
                now_ms=now,
            )
            if not picked:
                continue
            text = oracle.reminder(picked, now)
            # Bracketed paste so a multi-word line lands as one input, as the
            # Introduce button does.
            if not await asyncio.to_thread(
                sup.write_bytes, b"\x1b[200~" + text.encode() + b"\x1b[201~\r"
            ):
                continue
            self._oracle_nudges[key] = oracle.Nudge(frozenset(str(m["id"]) for m in picked), now)
            self.bus.publish({"event_type": "OracleNudge", "session_key": key, "text": text})

    def _archive_swept(self, key: str) -> None:
        """Archive a session whose terminal is gone (auto-sweep)."""
        self._set_lifecycle(key, "archived")
        self.approvals.drop_session(key)


def _pid_alive(pid: int) -> bool:
    """Whether a process is still running. `kill(pid, 0)` doesn't signal; it just
    checks existence/permission. ESRCH = gone; EPERM = alive but not ours."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _acquire_home_lock() -> Path:
    """Claim this DUCKTERM_HOME for one server. Writes a pidfile; if it already
    names a live process, raise so the second server fails loudly instead of
    silently sharing the DB and tmux panes. Returns the pidfile path."""
    home = instance.home()
    home.mkdir(parents=True, exist_ok=True)
    lock = home / "server.pid"
    if lock.exists():
        try:
            other = int(lock.read_text().strip() or "0")
        except ValueError:
            other = 0
        if other and other != os.getpid() and _pid_alive(other):
            raise SystemExit(
                f"another DuckTerm server (pid {other}) already owns "
                f"{home} — run it with a distinct DUCKTERM_INSTANCE, or stop that one."
            )
        # Stale pidfile (process gone / crashed): reclaim it.
    lock.write_text(str(os.getpid()))
    return lock


def _release_home_lock(lock: Path) -> None:
    # Only remove the pidfile if it's still ours (avoid deleting a lock a
    # replacement server already reclaimed).
    with contextlib.suppress(OSError, ValueError):
        if int(lock.read_text().strip() or "0") == os.getpid():
            lock.unlink()


def _branch_name(name: str | None) -> str:
    """Auto-branch for a new session: a slug of the session name, namespaced
    under duckterm/. Falls back to a timestamp when there's no name."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return f"duckterm/{slug}" if slug else f"duckterm/{int(time.time())}"
