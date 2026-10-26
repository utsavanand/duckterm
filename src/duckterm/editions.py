"""Desktop feature descriptions, independent of credentials and stored preferences.

Lite and Code describe the intended subsets of existing features. Only Full is
active until UI, routes, instructions and background work enforce those subsets.
There is deliberately no environment, settings or request-based edition switch.
"""

from dataclasses import dataclass
from typing import Literal

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
CODE = EditionConfig(
    "code",
    LITE.features
    | {
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
    },
)
FULL = EditionConfig("full", CODE.features | {"oracle", "remote_workspaces", "artifacts"})

# Keep today's behavior. Do not activate the smaller presets before enforcement.
# Planned Checks, PR review and Ducky are not advertised as implemented features.
CURRENT_EDITION = FULL
