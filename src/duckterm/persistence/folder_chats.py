"""Private folder conversations with recoverable folder rename/delete operations."""

import json
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from duckterm.core.oracle import CHAT_LIMIT
from duckterm.helpers.private_files import private_read, private_write

_LOCK = threading.RLock()


def valid_folder(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 4096
        and "\x00" not in value
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def within(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "/")


class FolderChats:
    def __init__(self, path: Path, folders: Callable[[], list[str]]) -> None:
        self.path = path
        self.folders = folders

    def _save(self, data: dict[str, Any]) -> None:
        private_write(self.path, json.dumps(data))

    def _load(self) -> dict[str, Any]:
        raw = private_read(self.path)
        try:
            data = json.loads(raw) if raw else {"chats": {}}
            if not isinstance(data, dict) or not isinstance(data.get("chats"), dict):
                raise ValueError("Invalid folder chat file")
            for key, entry in data["chats"].items():
                if (
                    not valid_folder(key)
                    or not isinstance(entry, dict)
                    or not isinstance(entry.get("id"), str)
                    or not isinstance(entry.get("messages"), list)
                ):
                    raise ValueError("Invalid folder conversation")
                if any(
                    not isinstance(message, dict)
                    or not isinstance(message.get("q"), str)
                    or not isinstance(message.get("a"), str)
                    or not isinstance(message.get("at"), int)
                    for message in entry["messages"]
                ):
                    raise ValueError("Invalid conversation message")
            pending = data.get("pending")
            if pending is not None and (
                not isinstance(pending, dict)
                or not valid_folder(pending.get("old"))
                or (pending.get("new") is not None and not valid_folder(pending["new"]))
            ):
                raise ValueError("Invalid folder operation")
        except (ValueError, TypeError):
            # Retain the damaged contents for recovery; the UI starts empty.
            if raw:
                private_write(
                    self.path.with_name(f"folder-chats-corrupt-{uuid.uuid4().hex}.json"), raw
                )
            data = {"chats": {}}
            self._save(data)
        # The DB mutation commits atomically. A durable intent lets the next read
        # finish the corresponding JSON change after an interrupted app restart.
        pending = data.pop("pending", None)
        if pending:
            old, new = pending["old"], pending["new"]
            if old not in self.folders():
                moved = {k: v for k, v in data["chats"].items() if within(k, old)}
                for key in moved:
                    del data["chats"][key]
                if new is not None:
                    for key, value in moved.items():
                        # Invalidate answers whose inference started before rename.
                        value["id"] = uuid.uuid4().hex
                        data["chats"][new + key[len(old) :]] = value
            self._save(data)
        return data

    def snapshot(self, folder: str) -> tuple[str, list[dict[str, Any]]]:
        with _LOCK:
            data = self._load()
            if folder not in self.folders():
                raise ValueError("Folder no longer exists")
            if folder not in data["chats"]:
                data["chats"][folder] = {"id": uuid.uuid4().hex, "messages": []}
                self._save(data)
            entry = data["chats"][folder]
            return str(entry["id"]), list(entry["messages"])

    def append(
        self, folder: str, identity: str, question: str, answer: str, at: int
    ) -> dict[str, Any]:
        with _LOCK:
            data = self._load()
            entry = data["chats"].get(folder)
            if folder not in self.folders() or not entry or entry["id"] != identity:
                raise ValueError("Folder changed while answering. Reopen it and ask again.")
            exchange = {"q": question, "a": answer, "at": at}
            entry["messages"] = (entry["messages"] + [exchange])[-CHAT_LIMIT:]
            self._save(data)
            return exchange

    def change(self, old: str, new: str | None, mutate: Callable[[], Any]) -> Any:
        """No awaits between intent, DB commit and completion; serialize writers."""
        with _LOCK:
            data = self._load()
            if old not in self.folders():
                return mutate()
            if new is not None and any(within(k, new) for k in data["chats"]):
                raise ValueError("Destination already has a folder conversation")
            data["pending"] = {"old": old, "new": new}
            self._save(data)
            try:
                return mutate()
            finally:
                self._load()
