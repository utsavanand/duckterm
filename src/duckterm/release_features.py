"""Local release switches, independent of edition eligibility and authorization.

Missing or invalid configuration disables experimental features. The file lives
in the instance home, never in a bundle or release asset. Restart after editing.
No settings, credentials, sessions or backups are removed when a flag is off.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from duckterm.helpers import instance

if TYPE_CHECKING:
    from duckterm.editions import EditionConfig

FLAGS = {
    "cross_computer_collaboration": "Cross-computer collaboration",
    "skill_kits": "Skill kits",
    "harness_management": "Harness installation and setup",
    "byok": "Bring your own API key",
    "byoc": "Bring your own cloud",
    "backup": "Backup",
}


def overrides() -> tuple[dict[str, bool], str | None]:
    try:
        data = json.loads((instance.home() / "feature-flags.json").read_text())
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError):
        return {}, "Invalid local flag configuration"
    if (
        not isinstance(data, dict)
        or set(data) != {"flags"}
        or not isinstance(data["flags"], dict)
        or any(k not in FLAGS or type(v) is not bool for k, v in data["flags"].items())
    ):
        return {}, "Invalid local flag configuration"
    return data["flags"], None


def enabled(flag: str) -> bool:
    values, _error = overrides()
    return flag in FLAGS and values.get(flag, False)


def require(flag: str) -> None:
    from duckterm.core.session_api import APIError

    if not enabled(flag):
        raise APIError(403, f"{FLAGS[flag]} is disabled by its feature flag")


def apply(edition: EditionConfig, values: dict[str, bool] | None = None) -> EditionConfig:
    from duckterm.editions import EditionConfig

    if values is None:
        values, _error = overrides()
    features = set(edition.features) - FLAGS.keys()
    for key in FLAGS:
        if values.get(key, False) and (key != "byoc" or "remote_workspaces" in features):
            features.add(key)
    if "byoc" not in features:
        features.discard("remote_workspaces")
    return EditionConfig(edition.name, frozenset(features))


def inventory(
    edition: EditionConfig, values: dict[str, bool], error: str | None
) -> dict[str, object]:
    return {
        "restart_required": True,
        "flags": [
            {
                "id": key,
                "name": name,
                "default": False,
                "enabled": edition.includes(key),
                "reason": error
                or (
                    "Not included in this edition"
                    if key == "byoc" and edition.name != "full"
                    else "Local override" if key in values else "Release default"
                ),
            }
            for key, name in FLAGS.items()
        ],
    }


def require_backup(destination: str | None = None) -> None:
    require("backup")
    if destination and destination.startswith("gs://"):
        from duckterm import editions
        from duckterm.core.session_api import APIError

        require("byoc")
        if not editions.configured().includes("remote_workspaces"):
            raise APIError(403, "Cloud backups are unavailable in this edition")
