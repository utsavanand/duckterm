"""Durable owner actions, forwarded only by the native owner-authenticated broker.

These commands never travel over the paired-computer exchange capability.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any

from duckterm.collaboration.folders import within
from duckterm.collaboration.store import Store, folder_path, identifier
from duckterm.core.session_api import APIError

if TYPE_CHECKING:
    from duckterm.collaboration.service import Service

ACTOR = "@owner"


def pending(service: Service) -> list[dict[str, Any]]:
    if not service.store:
        return []
    return [
        {
            "operation": json.loads(r["operation"]),
            "result": json.loads(r["result"]) if r["result"] else None,
            "attempted": bool(r["attempted"]),
        }
        for r in service.store.conn.execute(
            "SELECT operation,result,attempted FROM local_outbox WHERE actor=? ORDER BY sequence",
            (ACTOR,),
        )
        if not r["result"] or json.loads(r["result"]).get("state") != "applied"
    ]


def excluded(service: Service, path: str) -> bool:
    return any(
        item["operation"].get("old") and within(path, item["operation"]["old"])
        for item in pending(service)
    )


def queue(service: Service, req: dict[str, Any]) -> dict[str, Any]:
    if not service.connected:
        raise APIError(409, "Connect this computer before changing the shared workspace")
    assert service.store
    action = req.get("action")
    if action not in {"create", "move", "delete"}:
        raise APIError(400, "Unsupported workspace action")
    old, new = folder_path(req.get("old", "")), folder_path(req.get("new", ""))
    if action == "create":
        old = ""
    if action == "delete":
        new = ""
    if (action != "create" and not old) or (action != "delete" and not new):
        raise APIError(400, "Choose a folder")
    if action == "move" and (old == new or within(new, old)):
        raise APIError(400, "Cannot move a folder into itself")
    if pending(service):
        raise APIError(409, "Finish or cancel the pending workspace change first")
    folders = service.cached("folders", [])
    if service.store.setting("coordinator"):
        folders = service.store.folder_snapshot()
    folder = next((f["id"] for f in folders if f["path"] == old), None)
    if action != "create" and folder is None:
        raise APIError(409, "Folder has not synchronized yet; retry after connecting")
    parent_path = new.rpartition("/")[0]
    parent = None
    while parent_path:
        parent = next((f["id"] for f in folders if f["path"] == parent_path), None)
        if parent:
            break
        parent_path = parent_path.rpartition("/")[0]
    operation = {
        "id": uuid.uuid4().hex,
        "workspace_id": service.store.setting("identity")["workspace_id"],
        "action": action,
        "folder_id": folder,
        "old": old,
        "new": new,
        "parent_id": parent,
        "parent_path": parent_path,
    }
    with service.store.conn:
        service.store.conn.execute(
            "INSERT INTO local_outbox(id,actor,operation) VALUES (?,?,?)",
            (operation["id"], ACTOR, json.dumps(operation)),
        )
    return {"pending": True, "id": operation["id"], "operation": operation}


def apply(store: Store, operation: dict[str, Any]) -> dict[str, Any]:
    if operation.get("workspace_id") != store.setting("workspace"):
        raise APIError(409, "Coordinator workspace changed; reconnect before editing")
    opid = identifier(operation.get("id"), "owner operation")
    digest = hashlib.sha256(json.dumps(operation, sort_keys=True).encode()).hexdigest()
    row = store.conn.execute(
        "SELECT digest,result FROM operations WHERE computer=? AND id=?", (ACTOR, opid)
    ).fetchone()
    if row:
        if row["digest"] != digest:
            raise APIError(409, "Owner operation ID was reused with different content")
        return dict(json.loads(row["result"]))
    action = operation.get("action")
    old, new = folder_path(operation.get("old", "")), folder_path(operation.get("new", ""))
    if action not in {"create", "move", "delete"}:
        raise APIError(400, "Unsupported workspace action")
    folder = identifier(operation.get("folder_id"), "folder") if action != "create" else ""
    if action != "create" and (not old or store.path(folder) != old):
        raise APIError(
            409, "Folder changed elsewhere; cancel this action and review the current tree"
        )
    if action != "delete" and not new:
        raise APIError(400, "Choose a destination folder")
    if operation.get("parent_id"):
        parent_id = identifier(operation["parent_id"], "destination parent")
        if store.path(parent_id) != operation.get("parent_path"):
            raise APIError(
                409, "Destination parent changed; cancel this action and review the current tree"
            )
    with store.transaction():
        if action == "delete":
            store.delete_folder(folder)
        else:
            parts = new.split("/")
            parent = None
            for part in parts[:-1]:
                parent = store.add_folder(part, parent)
            if action == "move":
                store.change_folder(folder, parent, parts[-1])
            else:
                folder = store.add_folder(parts[-1], parent)
        result = {"id": opid, "state": "committed", "action": action, "old": old, "new": new}
        store.conn.execute(
            "INSERT INTO operations VALUES (?,?,?,?)", (ACTOR, opid, digest, json.dumps(result))
        )
    return result


def acknowledge(service: Service, req: dict[str, Any]) -> dict[str, Any]:
    if not service.store:
        raise APIError(409, "Computer is not connected")
    opid = identifier(req.get("id"), "owner operation")
    row = service.store.conn.execute(
        "SELECT result FROM local_outbox WHERE actor=? AND id=?", (ACTOR, opid)
    ).fetchone()
    if not row:
        raise APIError(404, "Pending workspace action not found")
    result = (
        {"state": "committed"}
        if req.get("committed") is True
        else {"state": "blocked", "error": str(req.get("error", "Workspace change failed"))[:2048]}
    )
    if row["result"] and json.loads(row["result"]).get("state") == "applied":
        return {"updated": False}
    with service.store.conn:
        service.store.conn.execute(
            "UPDATE local_outbox SET result=? WHERE id=?", (json.dumps(result), opid)
        )
    return {"updated": True}


def attempt(service: Service, req: dict[str, Any]) -> dict[str, Any]:
    if not service.store:
        raise APIError(409, "Computer is not connected")
    opid = identifier(req.get("id"), "owner operation")
    with service.store.conn:
        changed = service.store.conn.execute(
            "UPDATE local_outbox SET attempted=1 WHERE id=? AND actor=? AND result IS NULL",
            (opid, ACTOR),
        ).rowcount
    return {"attempted": bool(changed)}


def cancel(service: Service, req: dict[str, Any]) -> dict[str, Any]:
    if not service.store:
        raise APIError(409, "Computer is not connected")
    opid = identifier(req.get("id"), "owner operation")
    row = service.store.conn.execute(
        "SELECT * FROM local_outbox WHERE actor=? AND id=?", (ACTOR, opid)
    ).fetchone()
    if not row:
        raise APIError(404, "Pending action not found")
    state = json.loads(row["result"]).get("state") if row["result"] else None
    if state == "committed" or (row["attempted"] and state not in {"blocked", "applied"}):
        raise APIError(409, "Delivery is unconfirmed; retry before canceling this action")
    with service.store.conn:
        service.store.conn.execute(
            "UPDATE local_outbox SET result=? WHERE id=?",
            (json.dumps({"state": "applied", "cancelled": True}), opid),
        )
    return {"cancelled": True}


def settle(service: Service) -> None:
    assert service.store
    service.store.conn.execute(
        "UPDATE local_outbox SET result=? WHERE actor=? AND result=?",
        (json.dumps({"state": "applied"}), ACTOR, json.dumps({"state": "committed"})),
    )
