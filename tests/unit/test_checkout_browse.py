from pathlib import Path

import pytest

from duckterm.helpers import browse


def test_create_folder_and_detect_hidden_files(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = browse.create(str(tmp_path), "my checkout 日本語")
    folder = Path(result["path"])
    assert folder.parent == tmp_path
    assert result["empty"] is True
    (folder / ".keep").write_text("data")
    assert browse.listing(str(folder))["empty"] is False
    with pytest.raises(ValueError, match="already exists"):
        browse.create(str(tmp_path), folder.name)
    assert (folder / ".keep").read_text() == "data"


@pytest.mark.parametrize("name", ["", ".", "..", "../escape", "/absolute", "a/b", "a\\b", "a\n"])
def test_create_rejects_paths_and_control_characters(tmp_path, monkeypatch, name):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(ValueError):
        browse.create(str(tmp_path), name)
    assert not list(tmp_path.iterdir())


def test_create_rejects_parent_symlink_outside_home(tmp_path, monkeypatch):
    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    (home / "link").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    with pytest.raises(ValueError):
        browse.create(str(home / "link"), "new")
    assert not list(outside.iterdir())
