"""Secret creation must be private even before an atomic replacement."""

import os
import stat
from pathlib import Path

import pytest

from duckterm.helpers import private_files


def test_private_at_creation_and_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private" / "credential"
    replace = os.replace
    modes: list[int] = []

    def observe(source: str, destination: Path) -> None:
        modes.append(stat.S_IMODE(Path(source).stat().st_mode))
        replace(source, destination)

    monkeypatch.setattr(os, "replace", observe)
    old_mask = os.umask(0)
    try:
        private_files.private_write(path, "first")
        private_files.private_write(path, "rotated")
    finally:
        os.umask(old_mask)
    assert modes == [0o600, 0o600]
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert private_files.private_read(path) == "rotated"


def test_symlink_cannot_redirect_secret(tmp_path: Path) -> None:
    target = tmp_path / "victim"
    target.write_text("untouched")
    link = tmp_path / "credential"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic link"):
        private_files.private_write(link, "secret")
    with pytest.raises(OSError):
        private_files.private_read(link)
    assert target.read_text() == "untouched"
