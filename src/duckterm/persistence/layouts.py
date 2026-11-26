"""Owner widget layouts, with recoverable folder rename/delete operations."""

import json
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from duckterm.helpers.private_files import private_read, private_write
from duckterm.persistence.folder_chats import valid_folder, within

TYPES = {
    "oracle": ("agents", "needs-you", "tokens", "mail", "backup", "remote", "folder-tasks"),
    "folder": ("folder-tasks", "folder-stats", "artifacts-by-kind"),
    "home": (
        "teams",
        "autopilot",
        "chat",
        "prs",
        "spend",
        "usage",
        "needs-you",
        "agents",
        "mail",
        "backup",
        "remote",
        "shortcuts",
    ),
}
# Home places widgets on a 12-column grid: (type, x, y, w, h).
HOME_COLUMNS = 12
HOME_DEFAULTS = (
    ("teams", 0, 0, 3, 2),
    ("autopilot", 0, 2, 3, 2),
    ("spend", 0, 4, 3, 8),
    ("prs", 3, 0, 5, 6),
    ("needs-you", 3, 6, 5, 6),
    ("chat", 8, 0, 4, 12),
)
_LOCK = threading.RLock()


def surface_slot(surface: str) -> str:
    if surface in ("oracle", "home"):
        return surface
    if surface.startswith("folder:") and valid_folder(surface[7:]):
        return "folder"
    raise ValueError("Invalid layout surface")


def defaults(surface: str) -> list[dict[str, Any]]:
    slot = surface_slot(surface)
    if slot == "home":
        return [
            {
                "id": kind,
                "type": kind,
                "slot": slot,
                "position": i,
                "params": {"x": x, "y": y, "w": w, "h": h},
            }
            for i, (kind, x, y, w, h) in enumerate(HOME_DEFAULTS)
        ]
    return [
        {
            "id": kind,
            "type": kind,
            "slot": slot,
            "position": i,
            "params": {"folder": surface[7:]} if slot == "folder" else {},
        }
        for i, kind in enumerate(TYPES[slot])
    ]


def validate(surface: str, instances: Any) -> list[dict[str, Any]]:
    slot = surface_slot(surface)
    if not isinstance(instances, list) or len(instances) > len(TYPES[slot]):
        raise ValueError("Invalid widget list")
    ids, kinds = set(), set()
    for i, item in enumerate(instances):
        if not isinstance(item, dict) or set(item) != {"id", "type", "slot", "position", "params"}:
            raise ValueError("Invalid widget instance")
        identity, kind = item["id"], item["type"]
        if (
            not isinstance(identity, str)
            or not 1 <= len(identity) <= 80
            or not isinstance(kind, str)
            or kind not in TYPES[slot]
            or identity in ids
            or kind in kinds
            or item["slot"] != slot
            or type(item["position"]) is not int
            or item["position"] != i
            or not _valid_params(slot, surface, item["params"])
        ):
            raise ValueError("Invalid widget type, slot, position, or parameters")
        ids.add(identity)
        kinds.add(kind)
    return instances


def _valid_params(slot: str, surface: str, params: Any) -> bool:
    if slot != "home":
        return bool(params == ({"folder": surface[7:]} if slot == "folder" else {}))
    if not isinstance(params, dict) or set(params) != {"x", "y", "w", "h"}:
        return False
    if any(type(params[key]) is not int for key in params):
        return False
    x: int = params["x"]
    y: int = params["y"]
    w: int = params["w"]
    h: int = params["h"]
    return x >= 0 and w >= 1 and x + w <= HOME_COLUMNS and 0 <= y <= 200 and 1 <= h <= 24


class Layouts:
    def __init__(self, path: Path, folders: Callable[[], list[str]]) -> None:
        self.path, self.folders = path, folders

    def _save(self, data: dict[str, Any]) -> None:
        private_write(self.path, json.dumps(data))

    def _load(self) -> dict[str, Any]:
        raw = private_read(self.path)
        try:
            data = json.loads(raw) if raw else {"layouts": {}}
            if not isinstance(data, dict) or not isinstance(data.get("layouts"), dict):
                raise ValueError("Invalid layouts file")
            for surface, entry in data["layouts"].items():
                if not isinstance(entry, dict) or not isinstance(entry.get("revision"), str):
                    raise ValueError("Invalid layout entry")
                validate(surface, entry.get("instances"))
            pending = data.get("pending")
            if pending is not None and (
                not isinstance(pending, dict)
                or not valid_folder(pending.get("old"))
                or (pending.get("new") is not None and not valid_folder(pending["new"]))
            ):
                raise ValueError("Invalid folder operation")
        except (ValueError, TypeError):
            if raw:
                private_write(self.path.with_name(f"layouts-corrupt-{uuid.uuid4().hex}.json"), raw)
            data = {"layouts": {}}
            self._save(data)
        pending = data.pop("pending", None)
        if pending:
            old, new = pending["old"], pending["new"]
            if old not in self.folders():
                for surface in list(data["layouts"]):
                    if not surface.startswith("folder:") or not within(surface[7:], old):
                        continue
                    entry = data["layouts"].pop(surface)
                    if new is not None:
                        folder = new + surface[7 + len(old) :]
                        for item in entry["instances"]:
                            item["params"] = {"folder": folder}
                        entry["revision"] = uuid.uuid4().hex
                        data["layouts"]["folder:" + folder] = entry
            self._save(data)
        return data

    def recover(self) -> None:
        with _LOCK:
            self._load()

    def get(self, surface: str) -> dict[str, Any]:
        slot = surface_slot(surface)
        with _LOCK:
            data = self._load()
            if slot == "folder" and surface[7:] not in self.folders():
                raise KeyError("Folder no longer exists")
            return dict(
                data["layouts"].get(
                    surface, {"revision": "default", "instances": defaults(surface)}
                )
            )

    def put(self, surface: str, instances: Any, revision: str) -> dict[str, Any]:
        with _LOCK:
            validate(surface, instances)
            if self.get(surface)["revision"] != revision:
                raise FileExistsError("Layout changed elsewhere. Reload the layout before editing.")
            data = self._load()
            entry = {"revision": uuid.uuid4().hex, "instances": instances}
            data["layouts"][surface] = entry
            self._save(data)
            return entry

    def change(self, old: str, new: str | None, mutate: Callable[[], Any]) -> Any:
        with _LOCK:
            data = self._load()
            if old not in self.folders():
                return mutate()
            data["pending"] = {"old": old, "new": new}
            self._save(data)
            try:
                return mutate()
            finally:
                self._load()
