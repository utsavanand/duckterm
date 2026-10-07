"""Owner-controlled recovery of local folder collisions and computer disconnects."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any

from duckterm.collaboration import owner
from duckterm.collaboration.folders import after_move, paths, snapshot, within
from duckterm.collaboration.store import folder_path
from duckterm.core.session_api import APIError

if TYPE_CHECKING:
    from duckterm.collaboration.service import Service


def fingerprint(state: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def resume(service: Service) -> None:
    saved = service.cached("folder_recovery")
    if not saved:
        return
    assert service.store
    state = snapshot(service.history)
    if state == saved["before"]:
        service.move_folder(saved["old"], saved["new"])
    elif state != saved["after"]:
        raise APIError(409, "Folders changed during recovery; review the current arrangement")
    if snapshot(service.history) != saved["after"]:
        raise APIError(409, "Folder recovery did not preserve membership")
    service.history.layouts.recover()
    service.history.folder_chats.recover()
    with service.store.transaction():
        service.cache("folder_recovery", None)
        service.cache("folder_conflict", None)


async def keep_separately(service: Service, req: dict[str, Any]) -> dict[str, Any]:
    async with service.lock:
        conflict = service.cached("folder_conflict")
        if not service.connected or not conflict or conflict["token"] != req.get("token"):
            raise APIError(409, "Refresh the folder conflict before continuing")
        state = snapshot(service.history)
        if fingerprint(state) != conflict["token"]:
            raise APIError(409, "Local folders changed; retry synchronization to review them")
        old, new = conflict["path"], folder_path(req.get("new", ""))
        reserved = [u[k] for u in conflict["updates"] for k in ("path", "local_path") if u[k]]
        if (
            not new
            or new in paths(state)
            or within(new, old)
            or any(within(p, new) or within(new, p) for p in reserved)
        ):
            raise APIError(409, "Choose a new, unused folder name outside the shared move")
        assert service.store
        with service.store.transaction():
            service.cache("recovery_share", {"new": new})
            service.cache(
                "folder_recovery",
                {"old": old, "new": new, "before": state, "after": after_move(state, old, new)},
            )
        resume(service)
    await service.sync()
    await service.sync()
    return {"kept_as": new, "error": service.error}


async def prepare_disconnect(service: Service) -> dict[str, Any]:
    async with service.lock:
        if not service.connected or not service.store:
            raise APIError(409, "Computer is not connected")
        if service.store.setting("coordinator"):
            raise APIError(409, "Disconnect the other computers; the coordinator stays running")
        saved = service.store.setting("disconnecting")
        if saved:
            return dict(saved)
        for item in owner.pending(service):
            if item["attempted"] and item["result"] is None:
                raise APIError(409, "Retry the unconfirmed folder change before disconnecting")
        resume(service)
        service.apply_folder_plan()
        identity = service.store.setting("identity")
        saved = {
            "id": uuid.uuid4().hex,
            "computer_id": identity["computer_id"],
            "workspace_id": identity["workspace_id"],
        }
        service.store.configure(disconnecting=saved)
        return dict(saved)


async def disconnect(service: Service, req: dict[str, Any]) -> dict[str, Any]:
    async with service.lock:
        if not service.store:
            raise APIError(409, "No connection to disconnect")
        saved = service.store.setting("disconnecting")
        if not saved or saved["id"] != req.get("id"):
            raise APIError(409, "Review this computer before disconnecting")
        if service.transport:
            await service.transport.close()
            service.transport = None
        with service.store.transaction():
            service.cache(
                "detached-history:" + saved["computer_id"], service.cached("questions", [])
            )
            service.store.conn.execute(
                "UPDATE local_outbox SET result=? WHERE actor!=? AND result IS NULL",
                (
                    json.dumps({"ok": False, "status": 410, "error": "Computer disconnected"}),
                    owner.ACTOR,
                ),
            )
            for item in owner.pending(service):
                service.store.conn.execute(
                    "UPDATE local_outbox SET result=? WHERE id=?",
                    (
                        json.dumps({"state": "applied", "disconnected": True}),
                        item["operation"]["id"],
                    ),
                )
            for key in (
                "cards",
                "questions",
                "folders",
                "local_grants",
                "folder_plan",
                "folder_conflict",
                "recovery_share",
            ):
                service.store.conn.execute("DELETE FROM local_cache WHERE key=?", (key,))
            # Keep completed requests/receipts for audit, but never replay an old
            # computer's unsent operations after pairing a new identity.
            service.store.configure(
                identity=None, connection=None, pairing=None, disconnecting=None
            )
        service.last_sync = 0
        service.error = None
        return {"disconnected": True}


def queue_shared_folder(service: Service) -> None:
    """The owner chose a separate folder; register it in the same shared tree."""
    saved = service.cached("recovery_share")
    if saved and not owner.pending(service):
        owner.queue(service, {"action": "create", "new": saved["new"]})
        service.cache("recovery_share", None)
