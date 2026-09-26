"""Server-side directory listing for the New Session path picker. The browser
can't open a native folder dialog, but the server runs on your machine, so it
can list directories for the UI to navigate.

Lists subdirectories of a path, flags which are git repos, and resolves '~'.
Read-only and confined to the user's home directory, so a forged request can't
enumerate arbitrary parts of the filesystem.
"""

import os
from pathlib import Path
from typing import Any


def listing(path: str | None) -> dict[str, Any]:
    home = Path.home().resolve()
    base = Path(path).expanduser().resolve() if path else home
    # Confine browsing to the home tree; anything outside snaps back to home.
    if base != home and not base.is_relative_to(home):
        base = home
    if not base.is_dir():
        base = home

    entries: list[dict[str, Any]] = []
    try:
        empty = not any(base.iterdir())
        children = sorted(
            (p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.name.lower(),
        )
    except OSError:
        empty = False
        children = []

    for child in children:
        entries.append({"name": child.name, "path": str(child), "is_git": _is_git(child)})

    # Don't expose a parent above home — navigation stops at the home root.
    parent = base.parent if base != home and base.parent != base else None
    return {
        "path": str(base),
        "parent": str(parent) if parent else None,
        "is_git": _is_git(base),
        "entries": entries,
        "empty": empty,
    }


def create(parent: str, name: str) -> dict[str, Any]:
    """Create one explicitly named folder under the browsable home tree."""
    home = Path.home().resolve()
    base = Path(parent).expanduser().resolve(strict=True)
    if not base.is_relative_to(home) or not base.is_dir():
        raise ValueError("Choose a parent folder inside this computer's home folder")
    if not name.strip() or name in (".", "..") or any(c in name for c in "/\\"):
        raise ValueError("Enter a folder name without slashes")
    if any(ord(c) < 32 for c in name):
        raise ValueError("Folder name contains unsupported characters")
    destination = base / name
    try:
        destination.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise ValueError("A file or folder with that name already exists") from exc
    return listing(str(destination))


def _is_git(p: Path) -> bool:
    return (p / ".git").exists() and os.access(p, os.R_OK)
