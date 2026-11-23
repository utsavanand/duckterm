#!/usr/bin/env python3
"""Verify signed/notarized DMGs match the corresponding signed update ZIPs.

Run before publishing. Mounts only the supplied images at temporary paths and
never installs or launches their apps. Produces no files in user workspaces.
"""
import argparse
import plistlib
import re
import runpy
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
installer = runpy.run_path(str(ROOT / "mac/build-installer.py"))
feed = runpy.run_path(str(ROOT / "scripts/build_update_feeds.py"))
run = installer["run"]


def validate_app_names(root):
    apps = list(root.glob("*.app"))
    if len(apps) != 1 or apps[0].is_symlink():
        raise ValueError("Expected exactly one app bundle in the installer")
    return apps[0]


def verify(archives, version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a final release version")
    for edition in ("lite", "code", "full"):
        name = f"DuckTerm-{edition.title()}-{version}-macos-arm64"
        dmg = archives / f"{name}.dmg"
        run("/usr/bin/codesign", "--verify", "--strict", str(dmg))
        run("/usr/bin/xcrun", "stapler", "validate", str(dmg))
        run(
            "/usr/sbin/spctl",
            "--assess",
            "--type",
            "open",
            "--context",
            "context:primary-signature",
            str(dmg),
        )
        with tempfile.TemporaryDirectory(prefix="duckterm-installer-verify-") as temporary:
            work = Path(temporary)
            extracted = work / "archive"
            run("/usr/bin/ditto", "-x", "-k", str(archives / f"{name}.zip"), str(extracted))
            original = validate_app_names(extracted)
            with installer["mounted"](dmg, work / "image", readonly=True) as mounted:
                app = validate_app_names(mounted)
                info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
                feed["validate_info"](info, edition, version)
                if app.name != original.name or installer["manifest"](app) != installer["manifest"](
                    original
                ):
                    raise ValueError("Installer app differs from the update ZIP")
                if not (mounted / "Applications").is_symlink() or (
                    mounted / "Applications"
                ).readlink() != Path("/Applications"):
                    raise ValueError("Missing Applications shortcut")
                if (
                    not (mounted / ".DS_Store").is_file()
                    or not (mounted / ".background/installer.png").is_file()
                ):
                    raise ValueError("Installer layout is missing")
                run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
        print(f"Verified {dmg.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    verify(args.archives.resolve(), args.version)
