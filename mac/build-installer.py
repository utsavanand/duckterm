#!/usr/bin/env python3
"""Package an already-signed app as a drag-to-Applications DMG.

Requires macOS and a logged-in Finder session for the saved window layout.
Does not launch/copy the app to Applications, sign with a release identity,
notarize, publish, or overwrite an existing output. ZIPs remain Sparkle inputs.
"""
import argparse
import contextlib
import hashlib
import os
import plistlib
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(*args, timeout=180):
    return subprocess.run(args, check=True, capture_output=True, timeout=timeout).stdout


def manifest(root):
    return {
        str(p.relative_to(root)): (
            ("link", os.readlink(p))
            if p.is_symlink()
            else ("file", hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode & 0o111)
        )
        for p in root.rglob("*")
        if p.is_symlink() or p.is_file()
    }


@contextlib.contextmanager
def mounted(image, mountpoint, readonly=False):
    # The image and mountpoint are always private build paths. Never select a
    # device by volume name: a user's disk might have the same display name.
    try:
        run(
            "/usr/bin/hdiutil",
            "attach",
            str(image),
            "-nobrowse",
            "-noautoopen",
            "-mountpoint",
            str(mountpoint),
            "-readonly" if readonly else "-readwrite",
        )
        yield mountpoint
    finally:
        if os.path.ismount(mountpoint):
            run("/usr/bin/hdiutil", "detach", str(mountpoint), timeout=60)


def build(app, output):
    app = app.resolve(strict=True)
    output = output.parent.resolve(strict=True) / output.name
    if app.suffix != ".app" or output.suffix != ".dmg":
        raise ValueError("Expected an .app input and a .dmg output")
    if output.exists() or output.is_symlink():
        raise ValueError("Refusing to overwrite an existing installer")
    if output.is_relative_to(app):
        raise ValueError("Installer must be outside the app")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info.get("DucktermEdition") not in ("lite", "code", "full"):
        raise ValueError("Expected a DuckTerm edition bundle")
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
    original = manifest(app)
    # Same filesystem permits atomic, no-overwrite publication with os.link.
    with tempfile.TemporaryDirectory(prefix=".duckterm-installer-", dir=output.parent) as temp:
        work = Path(temp)
        staging = work / "contents"
        staging.mkdir()
        run("/usr/bin/ditto", str(app), str(staging / app.name))
        (staging / "Applications").symlink_to("/Applications")
        background = staging / ".background"
        background.mkdir()
        run(
            "/usr/bin/swift",
            str(ROOT / "installer-background.swift"),
            str(background / "installer.png"),
            app.stem,
        )
        writable = work / "layout.dmg"
        run(
            "/usr/bin/hdiutil",
            "create",
            "-srcfolder",
            str(staging),
            "-fs",
            "HFS+",
            "-format",
            "UDRW",
            "-volname",
            app.stem,
            str(writable),
        )
        mountpoint = work / "mounted"
        with mounted(writable, mountpoint):
            run(
                "/usr/bin/osascript",
                str(ROOT / "installer-layout.applescript"),
                str(mountpoint),
                app.name,
                timeout=60,
            )
            if not (mountpoint / ".DS_Store").is_file():
                raise ValueError("Finder did not save the installer layout")
        compressed = work / "installer.dmg"
        run(
            "/usr/bin/hdiutil",
            "convert",
            str(writable),
            "-format",
            "UDZO",
            "-imagekey",
            "zlib-level=9",
            "-o",
            str(compressed),
        )
        run("/usr/bin/hdiutil", "verify", str(compressed))
        with mounted(compressed, mountpoint, readonly=True):
            if os.readlink(mountpoint / "Applications") != "/Applications":
                raise ValueError("Installer Applications shortcut is invalid")
            if manifest(mountpoint / app.name) != original:
                raise ValueError("Packaged app differs from the signed input")
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(mountpoint / app.name))
        if manifest(app) != original:
            raise ValueError("Input app changed during packaging")
        os.link(compressed, output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(build(args.app, args.output))
