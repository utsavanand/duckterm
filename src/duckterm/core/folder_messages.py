"""Owner messages from a reviewed folder picker reuse the session inbox broker."""

import time
from typing import TYPE_CHECKING, Any

from duckterm.core.session_api import APIError, _text
from duckterm.persistence.folder_chats import within

if TYPE_CHECKING:
    from duckterm.persistence.history import HistoryStore


class FolderMessages:
    def __init__(self, history: "HistoryStore") -> None:
        self.history = history

    def recipients(self, folder: str) -> dict[str, Any]:
        identity, _ = self.history.folder_chats.snapshot(folder)
        sessions = self.history.session_api.broadcast_targets(folder)
        return {
            "identity": identity,
            "sessions": sessions,
            "folders": [
                {
                    "path": path,
                    "name": path.removeprefix(folder + "/"),
                    "recipients": self.history.session_api.broadcast_targets(path),
                }
                for path in self.history.folders()
                if path != folder and within(path, folder)
            ],
        }

    def send(self, folder: str, req: dict[str, Any]) -> dict[str, Any]:
        if set(req) != {"identity", "target", "text", "request_key", "recipients"}:
            raise APIError(400, "Expected identity, target, text, request_key and recipients")
        text = _text(req["text"], "text", 16384)
        request_key = _text(req["request_key"], "request_key", 64)
        target = req["target"]
        if (
            not isinstance(target, dict)
            or set(target) != {"kind", "id"}
            or target["kind"] not in ("session", "folder")
            or not isinstance(target["id"], str)
        ):
            raise APIError(400, "Invalid recipient")
        identity, messages = self.history.folder_chats.snapshot(folder)
        if req["identity"] != identity:
            raise APIError(409, "Folder changed. Reopen it before sending.")
        for message in messages:
            old = message.get("dispatch", {})
            if old.get("request_key") == request_key:
                if message["q"] != text or old["target"] != target:
                    raise APIError(409, "request_key already used for different content")
                return message
        broker = self.history.session_api
        scope = folder
        if target["kind"] == "folder":
            scope = target["id"]
            if scope == folder or not within(scope, folder) or scope not in self.history.folders():
                raise APIError(400, "Select a subfolder of this folder")
        candidates = broker.broadcast_targets(scope)
        if target["kind"] == "session":
            candidates = [r for r in candidates if r["session_id"] == target["id"]]
        eligible = [r for r in candidates if r["eligible"]]
        expected = req["recipients"]
        if (
            not isinstance(expected, list)
            or not all(isinstance(key, str) for key in expected)
            or sorted(expected) != sorted(r["session_id"] for r in eligible)
        ):
            raise APIError(
                409, "Recipients changed. Choose the recipient again and review before sending."
            )
        if not eligible:
            raise APIError(409, "No eligible inbox in this scope")
        # There is no await between scope validation and enqueue. A retry uses
        # the same broker key even if the process stopped before saving JSON.
        recipients = []
        if target["kind"] == "session":
            row = eligible[0]
            mid = broker.owner_message(
                row["session_id"], text, request_key=f"folder:{identity}:{request_key}"
            )
            recipients = [
                {
                    "session_id": row["session_id"],
                    "name": row["name"],
                    "message_id": mid,
                    "status": "queued",
                    "answer": None,
                }
            ]
        else:
            result = broker.broadcast(
                scope, {"text": text, "request_key": f"folder:{identity}:{request_key}"}
            )
            recipients = [
                {
                    "session_id": r["session_id"],
                    "name": r["name"],
                    "message_id": r["message_id"],
                    "status": "queued",
                    "answer": None,
                }
                for r in result["results"]
                if r["status"] == "queued"
            ]
        return self.history.folder_chats.append(
            folder,
            identity,
            text,
            "",
            int(time.time() * 1000),
            {
                "request_key": request_key,
                "target": target,
                "label": eligible[0]["name"] if target["kind"] == "session" else scope,
                "recipients": recipients,
            },
        )
