"""Owner-authenticated transfer endpoints used by the desktop's saved-host bridge."""

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm import transfers
from duckterm.agents import tmux

if TYPE_CHECKING:
    from duckterm.server import Server


def source_session(server: "Server", key: str) -> tuple[dict[str, Any], dict[str, str]]:
    row = server.history.session(key)
    if not row or row.get("state") not in ("stopped", "terminated"):
        raise ValueError("Stop the source session before reviewing or moving it")
    if tmux.session_exists(tmux.target_for(key)):
        raise ValueError("Source process is still running; wait for it to stop")
    row = {**row, "cwd": row.get("worktree_path") or row.get("cwd")}
    if not row["cwd"]:
        raise ValueError("Source project is unavailable")
    return row, transfers.conversation(row, server.history.session_id_for(key))


async def dispatch(server: "Server", operation: str, req: dict[str, Any]) -> dict[str, Any]:
    identifier = str(req.get("id", ""))
    source_key = str(req.get("source_session") or "")
    if operation in ("preview", "prepare"):
        conv = None
        source = str(req.get("source", ""))
        if source_key:
            row, conv = source_session(server, source_key)
            source = row["cwd"]
        selected = req.get("selected", [])
        if not isinstance(selected, list) or any(not isinstance(p, str) for p in selected):
            raise ValueError("Invalid ignored-file selection")
        if operation == "preview":
            snapshot = await asyncio.to_thread(transfers.scan, Path(source), selected)
            return {
                **snapshot,
                "conversation": {k: v for k, v in (conv or {}).items() if k != "transcript"},
            }
        if conv and req.get("conversation_sha256") != conv["sha256"]:
            raise ValueError("Conversation changed since review; review again")
        prior = transfers.session_transfer(source_key) if source_key else None
        if prior and prior["id"] != identifier:
            raise ValueError(
                "This session already has a transfer. Reopen its saved draft "
                "or explicitly Continue locally first."
            )
        if source_key in server._transfer_sources:
            raise ValueError("This session is already being prepared for transfer")
        if source_key:
            server._transfer_sources.add(source_key)
        try:
            return await asyncio.to_thread(
                transfers.prepare,
                identifier,
                source,
                selected,
                str(req.get("fingerprint", "")),
                conv,
                source_key or None,
            )
        finally:
            server._transfer_sources.discard(source_key)
    if operation == "chunk":
        return await asyncio.to_thread(transfers.chunk, identifier, int(req["offset"]))
    if operation == "preflight":
        dest = transfers.destination_path(str(req["destination"]))
        runtime = str(req.get("runtime") or "") or None
        transfers.check_runtime(str(req.get("command") or ""), runtime)
        transfers.check_requirements(req.get("requirements", []), req.get("linux_compatible", True))
        return {
            "destination": str(dest),
            "runtime": "available",
            "disk_free": shutil.disk_usage(dest.parent).free,
            "platform": sys.platform,
        }
    if operation == "begin":
        return await asyncio.to_thread(
            transfers.begin,
            identifier,
            str(req["destination"]),
            int(req["bytes"]),
            str(req["sha256"]),
        )
    if operation == "receive":
        return await asyncio.to_thread(
            transfers.receive, identifier, int(req["offset"]), str(req["data"])
        )
    if operation == "finish":
        return await asyncio.to_thread(transfers.finish, identifier)
    if operation == "clone":
        return await asyncio.to_thread(
            transfers.clone,
            identifier,
            str(req["url"]),
            str(req.get("branch") or ""),
            str(req["destination"]),
        )
    if operation == "status":
        return await asyncio.to_thread(transfers.status, identifier)
    if operation == "link":
        return await asyncio.to_thread(
            transfers.mark_moved, identifier, str(req["target"]), str(req["session_key"])
        )
    if operation == "continue":
        # Explicit recovery action: continuing locally is a separate continuation.
        state = transfers.session_transfer(str(req["source_session"]))
        if state:
            with transfers.locked(state["id"]) as directory:
                state = transfers.read_state(directory)
                transfers.save(directory, {**state, "stage": "continued-locally"})
        return {"released": True}
    if operation == "launch":
        from duckterm.server import _build_runtime

        # Serialise claims on the event loop; the durable journal guards restarts.
        if identifier in server._transfer_launches:
            raise ValueError("Launch is in progress; check transfer status")
        server._transfer_launches.add(identifier)
        try:
            old = transfers.status(identifier)
            if old.get("stage") in ("launching", "launched"):
                key = str(old["session_key"])
                if server.history.session(key) and tmux.session_exists(tmux.target_for(key)):
                    return transfers.mark_launched(identifier)
                raise ValueError(
                    "A launch was already attempted. Check the destination session; "
                    "it will not be launched twice"
                )
            state = transfers.claim_launch(
                identifier,
                str(req.get("command") or ""),
                str(req.get("name") or ""),
                str(req.get("prompt") or ""),
            )
            runtime = (state.get("conversation") or {}).get("runtime")
            key = await server.orchestrator.launch(
                runtime=_build_runtime(runtime, state["command"]),
                cwd=state["destination"],
                session_key=state["session_key"],
                name=state["name"],
                prompt=state["prompt"],
                test=req.get("test") is True,
            )
            if state["name"]:
                server.history.set_meta(key, name=state["name"])
            await asyncio.sleep(1)
            if not tmux.session_exists(tmux.target_for(key)):
                raise ValueError(
                    "Destination agent exited before attachment; "
                    "inspect its terminal before continuing"
                )
            return transfers.mark_launched(identifier)
        finally:
            server._transfer_launches.discard(identifier)
    raise ValueError("Unknown transfer operation")


async def handle(
    server: "Server", writer: asyncio.StreamWriter, operation: str, body: bytes
) -> None:
    from duckterm.transport.httpio import write_json as _write_json

    try:
        req = json.loads(body)
        if not isinstance(req, dict):
            raise ValueError("Expected transfer parameters")
        result = await dispatch(server, operation, req)
        await _write_json(writer, 200, result)
    except (OSError, ValueError, KeyError, RuntimeError, TimeoutError) as exc:
        await _write_json(writer, 400, {"error": str(exc)})
