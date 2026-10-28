import json
from pathlib import Path

import pytest

from duckterm.agents.hook_files import MARKER, install_file, uninstall_file
from duckterm.agents.hooks_install import install, uninstall
from duckterm.harnesses import REGISTRY
from duckterm.runtimes.base import HookSpec


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    return tmp_path.resolve() / ".opencode/plugins/duckterm.js"


def test_owned_install_upgrade_and_repeated_remove_preserve_neighbors(plugin):
    install_file(plugin, MARKER + b"export default 1;\n")
    original = plugin.stat().st_ino
    install_file(plugin, MARKER + b"export default 1;\n")
    assert plugin.stat().st_ino == original
    neighbor = plugin.with_name("user.js")
    neighbor.write_text("user plugin")
    install_file(plugin, MARKER + b"export default 2;\n")
    assert plugin.read_bytes().endswith(b"2;\n")
    uninstall_file(plugin)
    uninstall_file(plugin)
    assert not plugin.exists()
    assert neighbor.read_text() == "user plugin"


@pytest.mark.parametrize("content", [b"user plugin", MARKER + b"fake ownership"])
def test_marker_without_receipt_never_authorizes_overwrite_or_removal(plugin, content):
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(content)
    for operation in (
        lambda: install_file(plugin, MARKER + b"new"),
        lambda: uninstall_file(plugin),
    ):
        with pytest.raises(ValueError, match="unowned or modified"):
            operation()
        assert plugin.read_bytes() == content


def test_modified_plugin_and_corrupt_receipt_are_preserved(plugin):
    install_file(plugin, MARKER + b"original")
    plugin.write_bytes(MARKER + b"owner edits")
    with pytest.raises(ValueError):
        install_file(plugin, MARKER + b"upgrade")
    assert plugin.read_bytes() == MARKER + b"owner edits"
    with pytest.raises(ValueError):
        uninstall_file(plugin)
    receipt = plugin.with_name("." + plugin.name + ".duckterm-owned.json")
    receipt.write_text("[]")
    with pytest.raises(ValueError):
        uninstall_file(plugin)
    assert plugin.exists()


@pytest.mark.parametrize("part", ["file", "receipt", "parent", "lock"])
def test_symlinks_never_redirect_install_or_uninstall(plugin, tmp_path, part):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged")
    plugin.parent.mkdir(parents=True)
    if part == "parent":
        plugin.parent.rmdir()
        plugin.parent.symlink_to(outside, target_is_directory=True)
    else:
        target = {
            "file": plugin,
            "receipt": plugin.with_name(".duckterm.js.duckterm-owned.json"),
            "lock": plugin.with_name(".duckterm.js.duckterm-lock"),
        }[part]
        target.symlink_to(sentinel)
    for operation in (
        lambda: install_file(plugin, MARKER + b"new"),
        lambda: uninstall_file(plugin),
    ):
        with pytest.raises(ValueError):
            operation()
        assert sentinel.read_text() == "unchanged"
    assert not (outside / "duckterm.js").exists()


def test_crash_between_payload_and_receipt_fails_closed(plugin):
    install_file(plugin, MARKER + b"v1")
    plugin.write_bytes(MARKER + b"v2")
    with pytest.raises(ValueError):
        install_file(plugin, MARKER + b"v2")
    with pytest.raises(ValueError):
        uninstall_file(plugin)


def test_file_drop_transport_uses_actual_global_config_and_never_json_parser(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    class Probe:
        name = "probe"
        hook_spec = HookSpec(
            global_rel=Path("unused"),
            repo_rel=Path(".opencode/plugins/probe.js"),
            transport="file-drop",
            payload=lambda: MARKER + b"export default {};",
            global_path=lambda: tmp_path.resolve() / "custom-config/plugins/probe.js",
        )

    monkeypatch.setitem(REGISTRY, "probe", Probe)
    path = install(global_scope=True, project_dir=tmp_path, agent="probe")
    assert path == tmp_path.resolve() / "custom-config/plugins/probe.js"
    assert path.read_bytes().startswith(MARKER)
    receipt = json.loads(path.with_name(".probe.js.duckterm-owned.json").read_text())
    assert receipt["name"] == "probe.js"
    uninstall(global_scope=True, project_dir=tmp_path, agent="probe")
    assert not path.exists()


def test_parent_replaced_after_validation_cannot_redirect_writes(plugin, tmp_path, monkeypatch):
    from duckterm.agents import hook_files

    install_file(plugin, MARKER + b"original")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / plugin.name
    sentinel.write_text("user plugin")
    original = hook_files._write
    replaced = False

    def replace_parent(name, data, directory):
        nonlocal replaced
        if not replaced:
            replaced = True
            plugin.parent.rename(plugin.parent.with_name("moved"))
            plugin.parent.symlink_to(outside, target_is_directory=True)
        original(name, data, directory)

    monkeypatch.setattr(hook_files, "_write", replace_parent)
    install_file(plugin, MARKER + b"upgrade")
    assert sentinel.read_text() == "user plugin"
    assert sorted(p.name for p in outside.iterdir()) == [plugin.name]


def test_relocated_home_keeps_managed_directories_guarded(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    actual = root / "actual/user"
    actual.mkdir(parents=True)
    alias = root / "linked"
    alias.symlink_to(actual.parent, target_is_directory=True)
    home = alias / "user"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    plugin = home / ".opencode/plugins/duckterm.js"
    install_file(plugin, MARKER + b"original")
    assert (actual / ".opencode/plugins/duckterm.js").read_bytes() == MARKER + b"original"
    uninstall_file(plugin)
    assert not plugin.exists()

    outside = root / "outside"
    outside.mkdir()
    plugin.parent.rename(plugin.parent.with_name("original-plugins"))
    plugin.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe plugin directory"):
        install_file(plugin, MARKER + b"must not escape")
    assert list(outside.iterdir()) == []
