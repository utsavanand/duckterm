"""Narrow HTTP surface for computer exchange and owner-controlled setup."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from duckterm.collaboration import owner as owner_commands
from duckterm.collaboration.service import Service
from duckterm.collaboration.store import PROTOCOL, folder_path, reference, split_reference
from duckterm.core.session_api import APIError


def body_json(body: bytes) -> dict[str, Any]:
    try:
        result = json.loads(body or b"{}")
    except (UnicodeDecodeError, ValueError) as exc:
        raise APIError(400, "Invalid collaboration JSON") from exc
    if not isinstance(result, dict):
        raise APIError(400, "Expected a JSON object")
    return result


def canonical_folder(service: Service, path: str) -> str | None:
    path = folder_path(path)
    if not path:
        return None
    store = service.ensure_store()
    folder = None
    for name in path.split("/"):
        folder = store.add_folder(name, folder)
    return folder


async def owner(service: Service, method: str, route: str, body: bytes) -> dict[str, Any]:
    req = body_json(body)
    store = service.store
    if route == "/collaboration/status" and method == "GET":
        identity = store.setting("identity", {}) if store else {}
        result = {
            "protocol": PROTOCOL,
            "enabled": service.connected,
            "coordinator": bool(store and store.setting("coordinator")),
            "computer_id": identity.get("computer_id"),
            "workspace_id": identity.get("workspace_id"),
            "last_sync": service.last_sync,
            "error": service.error,
            "background_available": False,
            "capabilities": ["discovery", "direct_messages"],
            "schema": {"file": "collaboration.sqlite", "version": 1},
            "pending_changes": owner_commands.pending(service),
            "folders": service.cached("folders", []),
        }
        if store and store.setting("coordinator"):
            result["computers"] = [
                dict(r) for r in store.conn.execute("SELECT id,name,seen_at,revoked FROM computers")
            ]
            result["folders"] = store.folder_snapshot()
        return result
    if route == "/collaboration/owner-queue" and method == "GET":
        return {"commands": owner_commands.pending(service)}
    if route == "/collaboration/owner-queue" and method == "POST":
        return owner_commands.queue(service, req)
    if route == "/collaboration/owner-result" and method == "POST":
        return owner_commands.acknowledge(service, req)
    if route == "/collaboration/owner-attempt" and method == "POST":
        return owner_commands.attempt(service, req)
    if route == "/collaboration/owner-cancel" and method == "POST":
        return owner_commands.cancel(service, req)
    if route == "/collaboration/sync" and method == "POST":
        return {"synced": await service.sync(), "error": service.error}
    if route == "/collaboration/preview" and method == "GET":
        return {"protocol": PROTOCOL, "sessions": service.cards(), "enabled": service.connected}
    if route == "/collaboration/initialize" and method == "POST":
        return service.initialize(req.get("name", ""))
    if route == "/collaboration/connect" and method == "POST":
        if not isinstance(req.get("invitation"), dict) or not isinstance(
            req.get("connection"), dict
        ):
            raise APIError(400, "Expected pairing invitation and connection")
        return await service.pair(req["invitation"], req["connection"])
    if not store or not store.setting("coordinator"):
        raise APIError(409, "Choose a coordinator first")
    if route == "/collaboration/owner-apply" and method == "POST":
        return owner_commands.apply(store, req)
    if route == "/collaboration/folder" and method == "POST":
        with store.transaction():
            folder = canonical_folder(service, req.get("path", ""))
        return {"folder_id": folder}
    if route == "/collaboration/folder-change" and method == "POST":
        old = folder_path(req.get("old", ""))
        new = folder_path(req.get("new", ""))
        if not old or not new:
            raise APIError(400, "Choose valid source and destination folders")
        folder = next(
            (r[0] for r in store.conn.execute("SELECT id FROM folders") if store.path(r[0]) == old),
            None,
        )
        if folder is None:
            raise APIError(404, "Folder has not synchronized with the workspace")
        parent_path, _, name = new.rpartition("/")
        with store.transaction():
            parent = canonical_folder(service, parent_path)
            store.change_folder(folder, parent, name)
        return {"moved": old, "to": new, "folder_id": folder}
    if route == "/collaboration/invite" and method == "POST":
        return store.invite(req.get("name", ""), req.get("bindings", []))
    if route == "/collaboration/bind" and method == "POST":
        with store.transaction():
            folder = canonical_folder(service, req.get("canonical_path", ""))
            if folder is None:
                raise APIError(400, "Choose a sidebar folder")
            store.bind(str(req.get("computer_id", "")), req.get("local_path", ""), folder)
        return {"folder_id": folder}
    if route == "/collaboration/place" and method == "POST":
        with store.transaction():
            folder = canonical_folder(service, req.get("folder", ""))
            store.place(reference(req.get("computer_id"), req.get("session_key")), folder)
        return {"folder_id": folder}
    if route == "/collaboration/revoke" and method == "POST":
        store.revoke(str(req.get("computer_id", "")))
        return {"revoked": True}
    raise APIError(404, "Unknown collaboration operation")


async def session(
    service: Service, method: str, path: str, headers: dict[str, str], body: bytes
) -> tuple[int, dict[str, Any]] | None:
    """Return None when the existing same-computer API owns the route."""
    if not service.connected:
        if method == "GET" and urllib.parse.urlsplit(path).path == "/api/v1/session/peers":
            status, result = service.history.session_api.handle(method, path, headers, body)
            result["cross_computer_status"] = "not_configured"
            result["reason"] = (
                "Connect computers in Settings → Collaboration for cross-computer discovery"
            )
            return status, result
        return None
    api = service.history.session_api
    key = api.authenticate(headers)
    parsed = urllib.parse.urlsplit(path)
    route = parsed.path.removeprefix("/api/v1/session")
    member = api._member(key)
    if owner_commands.excluded(service, member["folder"]) and route != "/self":
        raise APIError(503, "This folder has a pending owner change; retry after synchronization")
    query = urllib.parse.parse_qs(parsed.query)
    req = body_json(body)
    if route == "/peers" and method == "GET":
        return 200, await service.discover(
            key, query.get("scope", ["self_folder"])[0], query.get("cursor", [""])[0]
        )
    if (
        route == "/questions"
        and method == "POST"
        and str(req.get("target_session_id", "")).startswith("peer:")
    ):
        computer, target_key = split_reference(req["target_session_id"])
        assert service.store
        if computer == service.store.setting("identity")["computer_id"]:
            return api._ask(key, {**req, "target_session_id": target_key}, headers)
        return await service.ask(key, req, headers.get("idempotency-key", ""))
    parts = route.split("/")
    if len(parts) in (3, 4) and parts[1] == "questions" and parts[2].startswith("fq-"):
        if len(parts) == 3 and method == "GET":
            return 200, await service.get(key, parts[2])
        if len(parts) == 4 and method == "POST":
            return await service.transition(key, parts[2], parts[3], req)
    if route == "/inbox" and method == "GET":
        return 200, await service.combined_inbox(key, query.get("before", [None])[0])
    return None
