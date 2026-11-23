"""Finish durable merge requests using the existing lifecycle sweep.

No timer or new operation store: fork rows and agent-merge receipts retain the
owner's close choice. Stop only after positive turn-end and empty-draft evidence.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING

from duckterm import memory_access
from duckterm.core.session_api import APIError
from duckterm.harnesses import runtime_for
from duckterm.runtimes.base import AT_REST_STATES

if TYPE_CHECKING:
    from duckterm.server import Server


async def finish(server: Server, source: str, target: str) -> str:
    row = server.history.session(source)
    if row is None:
        return "source_deleted"
    if row["state"] == "merged":
        return "closed"
    if (
        server.archives.pending(source)
        or server.restarts.pending(source)
        or source in server._transfer_sources
    ):
        return "other_operation_pending"
    # Never close a source whose destination can no longer retrieve its memory.
    try:
        access = memory_access.authorize(server, target, source)
    except APIError:
        return "memory_unavailable"
    sup = server.orchestrator.get(source)
    if sup is not None:
        if not row.get("launched"):
            return "terminal_not_owned"
        native = server.history.native_identity(source)
        running = await asyncio.to_thread(getattr, sup, "running")
        if (
            server.orchestrator.get(source) is not sup
            or server.history.native_identity(source) != native
        ):
            return "source_changed"
        if not running:
            # A completed supervisor can remain registered until the service
            # restarts. Its terminal has already ended, so there is no draft
            # to discard. Do not mistake an uncertain live state for closure.
            row = server.history.session(source) or {}
            if row.get("state") not in AT_REST_STATES:
                return "terminal_unavailable"
            sup = None
    if sup is not None:
        if not server.restarts.turn_finished(source):
            return "waiting_for_turn"
        event = server.history.latest_turn_event(source)
        stamp = sup.last_owner_input_ms
        native = server.history.native_identity(source)
        runtime = runtime_for(str(row.get("runtime") or "generic"), str(row.get("command") or ""))
        if not await server.restarts.draft_free(sup, runtime):
            return "draft_not_empty"
        # The prompt read yields. A new turn, keystroke, process or sharing change
        # invalidates permission to stop; do not infer idleness from the screen.
        if (
            server.orchestrator.get(source) is not sup
            or sup.last_owner_input_ms != stamp
            or server.history.latest_turn_event(source) != event
            or server.history.native_identity(source) != native
            or not server.restarts.turn_finished(source)
            or server.archives.pending(source)
            or server.restarts.pending(source)
            or source in server._transfer_sources
        ):
            return "source_changed"
        try:
            if memory_access.authorize(server, target, source) != access:
                return "memory_unavailable"
        except APIError:
            return "memory_unavailable"
        if not await server.orchestrator.stop(source):
            return "terminal_unavailable"
    elif row["state"] not in AT_REST_STATES:
        return "terminal_unavailable"
    try:
        if memory_access.authorize(server, target, source) != access:
            return "memory_unavailable"
    except APIError:
        return "memory_unavailable"
    server.history._conn.execute(
        "UPDATE sessions SET merged_into=? WHERE session_key=?", (target, source)
    )
    server.history._conn.commit()
    server._set_lifecycle(source, "merged")
    server.approvals.drop_session(source)
    return "closed"


async def recover(server: Server) -> None:
    """Called by the existing sweep, including after a service restart."""
    conn = server.history._conn
    requests = [
        (r["child"], r["parent"])
        for r in conn.execute(
            "SELECT m.child,m.parent FROM fork_merges m JOIN sessions s ON s.session_key=m.child "
            "WHERE keep_open=0 AND message_id IS NOT NULL AND s.state!='merged'"
        )
    ]
    requests += [
        (r["session_key"], json.loads(r["text"])["target"])
        for r in conn.execute(
            "SELECT d.session_key,d.text FROM digest_items d "
            "JOIN sessions s ON s.session_key=d.session_key "
            "WHERE d.bucket='agent_merge_v1' AND json_extract(d.text,'$.closeSource')=1 "
            "AND s.state!='merged'"
        )
    ]
    for source, target in requests:
        try:
            async with server._merge_lock:
                await finish(server, source, target)
        except (APIError, OSError) as exc:
            print(f"[duckterm] merge close for {source} deferred: {exc}", file=sys.stderr)
