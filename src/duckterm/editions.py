"""Immutable desktop capabilities, selected at startup without migrating data."""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from duckterm.helpers import instance

EditionName = Literal["lite", "code", "full"]


@dataclass(frozen=True)
class EditionConfig:
    name: EditionName
    features: frozenset[str]

    def includes(self, feature: str) -> bool:
        """Feature availability is not authorization or proof of provider support."""
        return feature in self.features

    def description(self) -> dict[str, object]:
        return {"id": self.name, "features": sorted(self.features)}


LITE = EditionConfig(
    "lite",
    frozenset(
        {
            "folders",
            "workstreams",
            "harnesses",
            "terminal",
            "shell",
            "messages",
            "forks",
            "session_status",
        }
    ),
)
SHARED_CODING = frozenset(
    {
        "explorer",
        "file_editing",
        "git_changes",
        "inbox",
        "folder_chat",
        "checkpoints",
        "summaries",
        "context",
        "handoff",
        "connectors",
    }
)
CODE = EditionConfig("code", LITE.features | SHARED_CODING | {"pr_review"})
FULL = EditionConfig("full", CODE.features | {"oracle", "remote_workspaces", "artifacts"})

# Existing installations keep Full unless explicitly configured otherwise.
CURRENT_EDITION = FULL
PRESETS: dict[str, EditionConfig] = {edition.name: edition for edition in (LITE, CODE, FULL)}


def configured(*, prefer_saved: bool = False) -> EditionConfig:
    """Environment wins; otherwise use this instance's last successful startup.

    Restarts prefer the saved selection because long-lived terminals may carry
    an environment inherited from a previous edition. Explicit CLI selection
    still wins. This is feature configuration, not a license boundary.
    Invalid configuration never silently enables the unrestricted product.
    """
    name = os.environ.get("DUCKTERM_EDITION")
    if prefer_saved or name is None:
        path = instance.home() / "edition.json"
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            if name is None:
                return CURRENT_EDITION
            data = {"id": name}
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read edition configuration at {path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Invalid edition configuration at {path}")
        name = data.get("id")
    if not isinstance(name, str) or name not in PRESETS:
        raise ValueError("Duckterm edition must be lite, code, or full")
    return PRESETS[name]


def remember(edition: EditionConfig) -> None:
    """Publish only after the server owns its home and successfully binds.

    Standalone CLI commands then use the same edition as the desktop. Sessions,
    preferences, grants and the shared database schema are left untouched.
    """
    path = instance.home() / "edition.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".edition-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump({"id": edition.name}, output)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def request_features(method: str, url: str, body: bytes = b"") -> tuple[str, ...]:
    """Classify optional HTTP entry points before they invoke any feature work.

    Authorization remains the dispatcher's responsibility. Query strings do not
    change a feature's availability; folder names may contain encoded slashes.
    Core terminal, lifecycle, approval, message and directory routes remain core.
    Routes match the raw path, so "/folders/a?b=/chat" reaches folder chat for a
    folder named "a?b=" although its parsed path is "/folders/a"; classify both.
    """
    # Either interpretation may reach a handler. Require both when they differ;
    # an allowed folder chat prefix must not hide a raw artifacts/PR suffix.
    paths = [urlsplit(url).path, url]
    if url.startswith("/sessions/"):
        # Prefix/suffix routes accept slashes inside the captured session key.
        # Check the final operation as well as nested routes such as /memory/*.
        paths.append("/sessions/_/" + url.rsplit("/", 1)[-1])
    features = (_path_feature(method, path, body) for path in paths)
    return tuple(dict.fromkeys(feature for feature in features if feature))


def _path_feature(method: str, path: str, body: bytes) -> str | None:
    if path.startswith("/api/v1/session/"):
        suffix = path.removeprefix("/api/v1/session/")
        if suffix == "artifacts" or suffix.startswith("artifacts/"):
            return "artifacts"
        if suffix.startswith("memory/"):
            return "context"
        return "inbox"
    if path.startswith(("/api/v1/collaboration/", "/collaboration/")):
        return "inbox"
    if path == "/review" or path.startswith("/review/"):
        return "pr_review"
    if re.fullmatch(r"/(sessions|folders)/.+/prs", path):
        return "pr_review"
    for prefix, capability in (
        ("/connectors", "connectors"),
        ("/transfers", "remote_workspaces"),
        ("/oracle", "oracle"),
        ("/relay", "oracle"),
        ("/voice", "oracle"),
        ("/snapshots", "checkpoints"),
        ("/broadcasts", "inbox"),
    ):
        if path == prefix or path.startswith(prefix + "/"):
            return capability
    exact = {
        "/control-tower": "oracle",
        "/checkpoint-settings": "checkpoints",
        "/agents-md/suggest": "context",
        "/session-inbox-counts": "inbox",
        "/folder-interactions": "inbox",
        "/analytics/mail": "inbox",
        "/tasks": "inbox",
    }
    if path in exact:
        return exact[path]
    if path.startswith("/file"):
        return "explorer" if method == "GET" else "file_editing"
    if path == "/fleet/ask":
        try:
            payload = json.loads(body or b"{}")
        except (ValueError, UnicodeError):
            payload = {}
        return "folder_chat" if isinstance(payload, dict) and payload.get("folder") else "oracle"
    folder = re.fullmatch(r"/folders/.+/(chat|artifacts|stats|recipients|dispatch|broadcast)", path)
    if folder:
        return {
            "chat": "folder_chat",
            "artifacts": "artifacts",
            "stats": "inbox",
            "recipients": "inbox",
            "dispatch": "inbox",
            "broadcast": "inbox",
        }[folder[1]]
    session = re.fullmatch(r"/sessions/[^/]+/(.+)", path)
    if not session:
        return None
    operation = session[1].split("/", 1)[0]
    feature = {
        "files": "explorer",
        "file-content": "explorer",
        "diff": "git_changes",
        "artifacts": "artifacts",
        "collaboration": "inbox",
        "enroll": "inbox",
        "inbox": "inbox",
        "memory": "context",
        "memory-claim": "context",
        "restart-preparation": "handoff",
        "handoff-review": "handoff",
        "handoff-approve": "handoff",
        "checkpoint": "checkpoints",
        "checkpoint-plan": "checkpoints",
        "checkpoints": "checkpoints",
        "checkpoint-attempts": "checkpoints",
        "digest": "summaries",
        "timeline": "context",
        "agent-merge": "handoff",
        "agent-merges": "handoff",
        "merge": "handoff",
        "merges": "handoff",
    }.get(operation)
    if operation == "annotations" and method == "POST":
        try:
            payload = json.loads(body or b"{}")
        except (ValueError, UnicodeError):
            payload = {}
        if isinstance(payload, dict) and "artifact_id" in payload:
            return "artifacts"
    return feature
