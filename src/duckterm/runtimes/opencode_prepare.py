"""Two-phase native preparation, with durable claims owned by the server loop.

This driver does not spawn a process, install configuration, or register a
runtime. The supervisor supplies its owned endpoint and fresh native observer.
Network work is off-loop; every returned write result must pass the journal's
generation and binding checks before it can affect the card.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from collections.abc import Awaitable, Callable

from duckterm.persistence import opencode_launch as journal
from duckterm.runtimes.opencode_identity import Selection
from duckterm.runtimes.opencode_protocol import NativeClient, OpenCodeError, UncertainWrite


async def prepare(
    conn: sqlite3.Connection,
    key: str,
    generation: str,
    client: NativeClient,
    selection: Callable[[journal.Launch], Awaitable[Selection]],
    owns_process: Callable[[], bool],
    *,
    timeout: float = 30,
) -> journal.Launch:
    """Finish or reconcile one launch. Never replay creation or prompt writes.

    ``selection`` must return authenticated native evidence for this owned process,
    not an HTTP selection acknowledgment. Its cached evidence expires in five
    seconds. The caller must gate owner input for the entire preparation and use
    fresh identity checks before any later targeted automation.
    """
    if not 0 < timeout <= 30:
        raise OpenCodeError("Invalid native preparation deadline")

    def current() -> journal.Launch:
        state = journal.read(conn, key, generation)
        if not owns_process() or state.directory != client.directory:
            raise OpenCodeError("Native preparation no longer owns this process or project")
        return state

    async def proof(state: journal.Launch) -> Selection:
        observation = await selection(state)
        current().require_selection(observation)
        return observation

    async with asyncio.timeout(timeout):
        state = current()
        await asyncio.to_thread(client.health)
        state = current()
        if state.phase == "creating":
            raise OpenCodeError(
                "Native creation is uncertain; automatic creation retry is disabled"
            )
        if state.phase == "prepared":
            state = journal.claim_create(conn, key, generation)
            result = await asyncio.to_thread(
                client.request, "POST", "/session", {"title": "DuckTerm " + key}
            )
            current()
            state = journal.bind_created(conn, key, generation, result)
        if state.phase == "bound":
            state = journal.claim_select(conn, key, generation)
            # The terminal may consume selection before its response is lost.
            # Only the observer can settle that uncertainty.
            with contextlib.suppress(UncertainWrite):
                await asyncio.to_thread(
                    client.request, "POST", "/tui/select-session", {"sessionID": state.session_id}
                )
            current()
        if state.phase == "selecting":
            observation = await proof(state)
            state = journal.claim_prompt(conn, key, generation, observation)
            if state.phase == "submitting":
                # Even a failed connection remains a claimed write. Only
                # reconcile it; never decide to resend it.
                with contextlib.suppress(OpenCodeError):
                    await asyncio.to_thread(
                        client.request,
                        "POST",
                        "/session/" + str(state.session_id) + "/prompt_async",
                        state.prompt_body(),
                    )
                current()
        while state.phase == "submitting":
            await proof(state)
            try:
                result = await asyncio.to_thread(
                    client.request,
                    "GET",
                    "/session/" + str(state.session_id) + "/message/" + str(state.message_id),
                )
            except OpenCodeError:
                current()
                await asyncio.sleep(0.1)
                continue
            current()
            state = journal.reconcile_prompt(conn, key, generation, result)
        if state.phase != "ready":
            raise OpenCodeError("Native preparation did not reach a verified state")
        await proof(state)
        return state
