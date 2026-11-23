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


def test_listing_marks_kit_folders_and_names_each_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kit = tmp_path / "uv-suite"
    kit.mkdir()
    (kit / "skill-kit.toml").write_text(
        'name = "uv-suite"\nversion = "1.0"\ndescription = "careful"\n'
    )
    (kit / "skill-kit.fast.toml").write_text('name = "uv-suite-fast"\nversion = "1.0"\n')
    (kit / "skill-kit.broken.toml").write_text("name = [\n")
    (tmp_path / "plain").mkdir()
    top = browse.listing(str(tmp_path))
    assert {e["name"]: e["is_kit"] for e in top["entries"]} == {"plain": False, "uv-suite": True}
    assert top["is_kit"] is False and top["kits"] == []
    inside = browse.listing(str(kit))
    assert inside["is_kit"] is True
    assert [(k["file"], k.get("name"), k.get("description")) for k in inside["kits"]] == [
        ("skill-kit.toml", "uv-suite", "careful"),
        ("skill-kit.broken.toml", None, None),
        ("skill-kit.fast.toml", "uv-suite-fast", None),
    ]
    assert "unreadable manifest" in inside["kits"][1]["error"]
