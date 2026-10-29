"""Recognize desktop-owned homes without adopting an existing custom workspace."""

import json
import os
import tempfile
from pathlib import Path

from duckterm.helpers import instance


def _marker_value() -> dict[str, object] | None:
    profile = instance.configured_profile()
    if profile is None or profile["profile_id"] == "full":
        return None  # The legacy Full workspace intentionally needs no migration.
    return {"format": 1, "profile_id": profile["profile_id"], "edition": profile["edition"]}


def validate_home() -> bool:
    """Return whether a new desktop home needs its marker, without writing.

    Call before acquiring a home lock or constructing any database-backed object.
    An existing unmarked directory belongs to its user; even an empty directory
    isn't evidence that it was created by this version of the desktop app.
    """
    expected = _marker_value()
    if expected is None:
        return False
    root = instance.home()
    if root.is_symlink():
        raise ValueError(f"Desktop workspace must not be a symlink: {root}")
    if not root.exists():
        return True
    marker = root / "profile.json"
    try:
        actual = json.loads(marker.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Existing workspace {root} is not owned by this desktop profile. "
            "Keep using its original custom instance; choose a new workspace explicitly. "
            "No existing data has been adopted."
        ) from exc
    if actual != expected:
        raise ValueError(f"Workspace {root} belongs to a different or newer desktop profile")
    return False


def remember_new_home() -> None:
    """Write only after the caller has bound the port and acquired home ownership."""
    value = _marker_value()
    if value is None:
        return
    root = instance.home()
    fd, temporary = tempfile.mkstemp(prefix=".profile-", dir=root)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.write("\n")
        # Exclusive publication: never replace another owner's marker.
        os.link(temporary, root / "profile.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
