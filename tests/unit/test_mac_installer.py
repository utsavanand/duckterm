"""Headless guard checks; real Finder/image checks live in mac/Tests."""

import importlib.util
import plistlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mac_installer", ROOT / "mac/build-installer.py")
assert spec and spec.loader
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def fixture_app(tmp_path):
    app = tmp_path / "DuckTerm Test.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps({"DucktermEdition": "full", "DucktermTestBuild": True})
    )
    return app


def test_builder_refuses_existing_output_without_running_any_commands(tmp_path, monkeypatch):
    app = fixture_app(tmp_path)
    output = tmp_path / "existing.dmg"
    output.write_bytes(b"existing installer")
    monkeypatch.setattr(installer, "run", lambda *a, **kw: pytest.fail("must not run commands"))
    with pytest.raises(ValueError, match="overwrite"):
        installer.build(app, output)
    assert output.read_bytes() == b"existing installer"
    output.unlink()
    output.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="overwrite"):
        installer.build(app, output)
    assert not (tmp_path / "missing").exists()


def test_output_parent_symlink_cannot_write_into_input_app(tmp_path, monkeypatch):
    app = fixture_app(tmp_path)
    link = tmp_path / "redirect"
    link.symlink_to(app, target_is_directory=True)
    monkeypatch.setattr(installer, "run", lambda *a, **kw: pytest.fail("must not run commands"))
    with pytest.raises(ValueError, match="outside"):
        installer.build(app, link / "result.dmg")
    assert not (app / "result.dmg").exists()


def test_invalid_signature_stops_before_packaging(tmp_path, monkeypatch):
    app = fixture_app(tmp_path)
    commands = []

    def fail_signature(*args, **kwargs):
        commands.append(args)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(installer, "run", fail_signature)
    with pytest.raises(subprocess.CalledProcessError):
        installer.build(app, tmp_path / "result.dmg")
    assert len(commands) == 1
    assert commands[0][0] == "/usr/bin/codesign"
    assert not (tmp_path / "result.dmg").exists()
    assert not list(tmp_path.glob(".duckterm-installer-*"))


@pytest.mark.parametrize("fail_attach", [False, True])
def test_mount_failure_detaches_only_its_private_mountpoint(tmp_path, monkeypatch, fail_attach):
    mountpoint = tmp_path / "unique-image"
    commands = []

    def run(*args, **kwargs):
        commands.append(args)
        if args[1] == "attach" and fail_attach:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(installer, "run", run)
    monkeypatch.setattr(installer.os.path, "ismount", lambda p: p == mountpoint)
    with (
        pytest.raises((RuntimeError, subprocess.CalledProcessError)),
        installer.mounted(tmp_path / "fixture.dmg", mountpoint),
    ):
        raise RuntimeError("layout failed")
    assert commands[-1] == ("/usr/bin/hdiutil", "detach", str(mountpoint))
    assert "-force" not in commands[-1]


def test_manifest_preserves_symlink_identity_and_executable_mode(tmp_path):
    file = tmp_path / "fixture"
    file.write_bytes(b"same bytes")
    link = tmp_path / "link"
    link.symlink_to("fixture")
    before = installer.manifest(tmp_path)
    file.chmod(0o755)
    assert installer.manifest(tmp_path) != before
    assert before["link"] == ("link", "fixture")
