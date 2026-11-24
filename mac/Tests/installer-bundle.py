#!/usr/bin/env python3
"""Build and inspect a disposable installer. Never launches or installs its app."""
import importlib.util
import os
import plistlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("installer", ROOT / "mac/build-installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_signed_app_and_shortcut_survive_readonly_image(self):
        with tempfile.TemporaryDirectory(prefix="duckterm-installer-test-") as directory:
            work = Path(directory)
            app = work / "DuckTerm Installer Test.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Resources").mkdir()
            (contents / "Info.plist").write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleIdentifier": "com.duckterm.test.installer",
                        "CFBundleExecutable": "Fixture",
                        "CFBundlePackageType": "APPL",
                        "CFBundleVersion": "0.0.1",
                        "DucktermEdition": "full",
                        "DucktermTestBuild": True,
                    }
                )
            )
            source = work / "fixture.c"
            source.write_text("int main(void) { return 0; }\n")
            installer.run("/usr/bin/clang", str(source), "-o", str(contents / "MacOS/Fixture"))
            (contents / "Resources/evidence.txt").write_text("preserve me\n")
            (contents / "Resources/evidence-link").symlink_to("evidence.txt")
            installer.run("/usr/bin/codesign", "--force", "--sign", "-", str(app))
            original = installer.manifest(app)
            output = work / "fixture.dmg"
            installer.build(app, output)
            self.assertEqual(installer.manifest(app), original)
            with installer.mounted(output, work / "inspect", readonly=True) as mounted:
                self.assertEqual(os.readlink(mounted / "Applications"), "/Applications")
                self.assertEqual(installer.manifest(mounted / app.name), original)
                self.assertGreater((mounted / ".DS_Store").stat().st_size, 0)
                self.assertTrue((mounted / ".background/installer.png").is_file())
                with self.assertRaises(OSError):
                    (mounted / "should-not-write").write_text("x")
            before = output.read_bytes()
            with self.assertRaises(ValueError):
                installer.build(app, output)
            self.assertEqual(output.read_bytes(), before)
            # Dangling output links cannot redirect or overwrite user data.
            link = work / "dangling.dmg"
            link.symlink_to(work / "absent")
            with self.assertRaises(ValueError):
                installer.build(app, link)
            self.assertFalse((work / "absent").exists())
            self.assertFalse(os.path.ismount(work / "inspect"))
            self.assertFalse(list(work.glob(".duckterm-installer-*")))


if __name__ == "__main__":
    unittest.main()
