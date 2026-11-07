"""Owner-authenticated transfer endpoints used by the desktop's saved-host bridge."""

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm import editions, transfers
from duckterm.agents import tmux
from duckterm.core.session_api import APIError

if TYPE_CHECKING:
    from duckterm.server import Server


_LOCAL_PARAMETERS = {
    "mkdir": {"parent", "name"},
    "repositories": {"page"},
    "preflight": {"id", "url", "branch", "destination", "auto_workspace", "command", "runtime"},
    "clone": {
        "id",
        "url",
        "branch",
        "destination",
        "github_repository",
        "auto_workspace",
        "attempt_id",
        # Existing clone forms also serialize these inert draft fields.
        "repository",
        "destination_source",
        "source",
        "selected",
    },
    "status": {"id"},
    "launch": {"id", "command", "name", "prompt", "test"},
}
_CLONE_STAGES = {"cloning", "verifying", "publishing", "failed", "ready", "launching", "launched"}
_TRANSFER_FIELDS = {
    "conversation",
    "source_session",
    "source",
    "fingerprint",
    "bytes",
    "sha256",
    "offset",
    "target",
}


def local_clone_only(server: "Server", operation: str, req: dict[str, Any]) -> bool:
    """A local-clone route never authorizes importing or moving a conversation."""
    # None is used by the existing pure preflight tests, never by HTTP dispatch.
    if server is None:
        return False
    server.require_feature(
        "terminal" if operation in editions.LOCAL_PROJECT_OPERATIONS else "remote_workspaces"
    )
    if server.edition.includes("remote_workspaces"):
        return False
    # Optional undefined values from the existing web form may cross WebKit as
    # null. They carry no transfer context; non-null transfer fields stay denied.
    absent = {
        "preflight": {"runtime", "requirements", "linux_compatible"},
        "clone": {"source_session", "fingerprint", "conversation_sha256"},
    }.get(operation, set())
    for field in absent:
        if req.get(field) is None:
            req.pop(field, None)
    if set(req) - _LOCAL_PARAMETERS[operation]:
        raise APIError(403, "Only local repository clone parameters are available in this edition")
    if operation == "clone" and (req.get("source", "") != "" or req.get("selected", []) != []):
        raise APIError(403, "Copying an existing project requires remote_workspaces")
    return True


def require_clone(state: dict[str, Any], identifier: str, *, empty: bool = False) -> None:
    if not state and empty:
        return
    if (
        state.get("id") != identifier
        or not isinstance(state.get("stage"), str)
        or state.get("stage") not in _CLONE_STAGES
        or not isinstance(state.get("url"), str)
        or not state["url"]
        or not isinstance(state.get("destination"), str)
        or not state["destination"]
        or _TRANSFER_FIELDS.intersection(state)
    ):
        raise APIError(403, "This operation is not a local repository clone")
    try:
        transfers.check_url(state["url"])
    except ValueError as exc:
        raise APIError(403, "This operation is not a local repository clone") from exc


async def source_session(server: "Server", key: str) -> tuple[dict[str, Any], dict[str, str]]:
    if server.restarts.running(key):
        raise ValueError("Wait for the session restart to finish before moving it")
    row = server.history.session(key)
    if not row or row.get("state") not in ("stopped", "terminated"):
        raise ValueError("Stop the source session before reviewing or moving it")
    if await asyncio.to_thread(tmux.session_exists, tmux.target_for(key)):
        raise ValueError("Source process is still running; wait for it to stop")
    if server.restarts.running(key):
        raise ValueError("Wait for the session restart to finish before moving it")
    row = {**row, "cwd": row.get("worktree_path") or row.get("cwd")}
    if not row["cwd"]:
        raise ValueError("Source project is unavailable")
    return row, transfers.conversation(row, server.history.session_id_for(key))


async def dispatch(server: "Server", operation: str, req: dict[str, Any]) -> dict[str, Any]:
    req = dict(req)
    restricted = local_clone_only(server, operation, req)
    identifier = str(req.get("id", ""))
    authorize = (lambda state: require_clone(state, identifier)) if restricted else None
    source_key = str(req.get("source_session") or "")
    if operation == "mkdir":
        from duckterm.helpers import browse

        return await asyncio.to_thread(browse.create, str(req["parent"]), str(req["name"]))
    if operation in ("preview", "prepare"):
        conv = None
        source = str(req.get("source", ""))
        if source_key:
            row, conv = await source_session(server, source_key)
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
        if restricted and identifier:
            require_clone(transfers.status(identifier), identifier, empty=True)
        automatic = req.get("auto_workspace") is True
        dest = (
            transfers.automatic_workspace(identifier, str(req["url"]))
            if automatic
            else transfers.destination_path(str(req["destination"]))
        )
        if automatic and dest.exists():
            prior = transfers.status(identifier)
            if restricted:
                require_clone(prior, identifier)
            if not (
                prior.get("destination") == str(dest)
                and prior.get("url") == req["url"]
                and prior.get("branch") == str(req.get("branch") or "")
                and prior.get("stage") in ("ready", "launching", "launched", "publishing")
            ):
                raise ValueError("Automatic workspace already exists; start a new session")
        parent = transfers.available_parent(dest.parent)
        runtime = str(req.get("runtime") or "") or None
        transfers.check_runtime(str(req.get("command") or ""), runtime)
        transfers.check_requirements(req.get("requirements", []), req.get("linux_compatible", True))
        return {
            "destination": str(dest),
            "runtime": "available",
            "disk_free": shutil.disk_usage(parent).free,
            "auto_workspace": automatic,
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
    if operation == "repositories":
        from duckterm import github_projects

        return await asyncio.to_thread(github_projects.list_repositories, int(req.get("page", 1)))
    if operation == "clone":
        return await asyncio.to_thread(
            transfers.clone,
            identifier,
            str(req["url"]),
            str(req.get("branch") or ""),
            str(req["destination"]),
            str(req.get("github_repository") or ""),
            req.get("auto_workspace") is True,
            str(req.get("attempt_id") or ""),
            authorize=(
                (lambda state: require_clone(state, identifier, empty=True)) if restricted else None
            ),
        )
    if operation == "status":
        clone_status = await asyncio.to_thread(transfers.status, identifier)
        if restricted:
            require_clone(clone_status, identifier, empty=True)
        return clone_status
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
            if authorize is not None:
                authorize(old)
            if old.get("stage") in ("launching", "launched"):
                key = str(old["session_key"])
                if server.history.session(key) is not None and await asyncio.to_thread(
                    tmux.session_exists, tmux.target_for(key)
                ):
                    return transfers.mark_launched(identifier, authorize=authorize)
                raise ValueError(
                    "A launch was already attempted. Check the destination session; "
                    "it will not be launched twice"
                )
            state = transfers.claim_launch(
                identifier,
                str(req.get("command") or ""),
                str(req.get("name") or ""),
                str(req.get("prompt") or ""),
                authorize=authorize,
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
            if not await asyncio.to_thread(tmux.session_exists, tmux.target_for(key)):
                raise ValueError(
                    "Destination agent exited before attachment; "
                    "inspect its terminal before continuing"
                )
            return transfers.mark_launched(identifier, authorize=authorize)
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
    except APIError as exc:
        await _write_json(writer, exc.status, {"error": str(exc)})
    except (OSError, ValueError, KeyError, RuntimeError, TimeoutError) as exc:
        await _write_json(writer, 400, {"error": str(exc)})
