"""Read packaged identity; an editable checkout without a stamp remains unknown."""

import importlib
import re
from typing import Any

from duckterm import __version__


def installed() -> dict[str, Any]:
    values: dict[str, Any] = dict.fromkeys(
        (
            "installed_commit",
            "installed_describe",
            "installed_branch",
            "installed_dirty",
            "installed_in_main",
            "installed_is_release",
        )
    )
    try:
        data = importlib.import_module("duckterm._build")
        if getattr(data, "VERSION", None) != __version__:
            return {**values, "installed_state": "unknown"}
        commit = getattr(data, "COMMIT", None)
        describe = getattr(data, "DESCRIBE", None)
        if (
            not isinstance(commit, str)
            or not re.fullmatch(r"[a-f0-9]{40}(?:[a-f0-9]{24})?", commit)
            or not isinstance(describe, str)
            or not describe
            or len(describe) > 1024
            or any(ord(c) < 32 for c in describe)
        ):
            return {**values, "installed_state": "unknown"}
        values["installed_commit"] = commit
        values["installed_describe"] = describe
        branch = getattr(data, "BRANCH", None)
        if isinstance(branch, str) and len(branch) <= 1024 and not any(ord(c) < 32 for c in branch):
            values["installed_branch"] = branch
        for field, attribute in (
            ("dirty", "DIRTY"),
            ("in_main", "IN_MAIN"),
            ("is_release", "IS_RELEASE"),
        ):
            value = getattr(data, attribute, None)
            if type(value) is bool:
                values["installed_" + field] = value
        if values["installed_is_release"] is True and not (
            values["installed_dirty"] is False
            and values["installed_in_main"] is True
            and getattr(data, "TAG", None) == "v" + __version__
        ):
            values["installed_is_release"] = None
    except (ImportError, OSError, SyntaxError):
        pass
    released = values["installed_is_release"]
    return {
        **values,
        "installed_state": (
            "released" if released is True else "unreleased" if released is False else "unknown"
        ),
    }
