#!/usr/bin/env python3
"""Exercise signed bundle ZIP extraction without launching the fixture app."""

import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def manifest(app):
    return {
        str(path.relative_to(app)): (
            ("link", os.readlink(path))
            if path.is_symlink()
            else (
                "file",
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mode & 0o111,
            )
        )
        for path in app.rglob("*")
        if path.is_symlink() or path.is_file()
    }


class ArchiveBundleTests(unittest.TestCase):
    def test_metadata_stays_outside_signed_bundle(self):
        with tempfile.TemporaryDirectory(prefix="duckterm-archive-test-") as directory:
            work = Path(directory)
            app = work / "Archive Fixture.app"
            binary = app / "Contents/MacOS/Fixture"
            resources = app / "Contents/Resources"
            binary.parent.mkdir(parents=True)
            resources.mkdir()
            (app / "Contents/Info.plist").write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleIdentifier": "com.duckterm.test.archive",
                        "CFBundleExecutable": "Fixture",
                        "CFBundlePackageType": "APPL",
                        "CFBundleVersion": "1",
                    }
                )
            )
            source = work / "fixture.c"
            source.write_text("int main(void) { return 0; }\n")
            run("/usr/bin/clang", str(source), "-o", str(binary))
            (resources / "data").write_text("archive metadata regression\n")
            (resources / "data-link").symlink_to("data")
            run(
                "/usr/bin/xattr",
                "-w",
                "com.duckterm.archive-test",
                "metadata",
                str(resources / "data"),
            )
            run(
                "/usr/bin/xattr",
                "-s",
                "-w",
                "com.duckterm.archive-test",
                "link-metadata",
                str(resources / "data-link"),
            )
            # Ad-hoc signature on a disposable fixture; no Developer ID/keychain.
            run("/usr/bin/codesign", "--force", "--sign", "-", str(app))
            before = manifest(app)
            archive = work / "fixture.zip"
            run("bash", str(ROOT / "mac/archive-bundle.sh"), str(app), str(archive))
            with zipfile.ZipFile(archive) as bundle:
                metadata = [name for name in bundle.namelist() if Path(name).name.startswith("._")]
                self.assertTrue(metadata, "Fixture must exercise AppleDouble metadata")
                self.assertTrue(all(name.startswith("__MACOSX/") for name in metadata))
            for method in ("ditto", "tar", "unzip"):
                with self.subTest(extractor=method):
                    destination = work / method
                    destination.mkdir()
                    if method == "ditto":
                        run("/usr/bin/ditto", "-x", "-k", str(archive), str(destination))
                    elif method == "tar":
                        run("/usr/bin/tar", "-xf", str(archive), "-C", str(destination))
                    else:
                        run("/usr/bin/unzip", "-q", str(archive), "-d", str(destination))
                    extracted = destination / app.name
                    self.assertEqual(manifest(extracted), before)
                    if method == "ditto":
                        actual = run(
                            "/usr/bin/xattr",
                            "-p",
                            "com.duckterm.archive-test",
                            str(extracted / "Contents/Resources/data"),
                        ).stdout.strip()
                        self.assertEqual(actual, "metadata")
                    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(extracted))
            self.assertEqual(manifest(app), before)
            for name, value in (("data", "metadata"), ("data-link", "link-metadata")):
                actual = run(
                    "/usr/bin/xattr",
                    "-s",
                    "-p",
                    "com.duckterm.archive-test",
                    str(resources / name),
                ).stdout.strip()
                self.assertEqual(actual, value)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            refusal = subprocess.run(
                ["bash", str(ROOT / "mac/archive-bundle.sh"), str(app), str(archive)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(refusal.returncode, 0)
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), digest)
            self.assertEqual(list(work.glob(".duckterm-archive.*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
