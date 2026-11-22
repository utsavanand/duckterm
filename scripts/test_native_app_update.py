#!/usr/bin/env python3
"""Exercise the real Sparkle installer in disposable native Test bundles.

Requires macOS with a graphical login and resolved mac/Package.swift. No
DuckTerm workspace, installed app, harness, Keychain key or owner token is used.
The test includes the production AppUpdater adapter. Backend version is a fixture;
the complete Test app and session reconnection remain release-candidate checks.
"""
import argparse
import http.server
import json
import plistlib
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPARKLE = ROOT / "mac/.build/artifacts/sparkle/Sparkle"
FRAMEWORKS = SPARKLE / "Sparkle.xcframework/macos-arm64_x86_64"
KEY_SOURCE = """import Foundation
import CryptoKit
let root = URL(fileURLWithPath: CommandLine.arguments[1])
let key = Curve25519.Signing.PrivateKey()
let secret = root.appendingPathComponent("key")
try key.rawRepresentation.base64EncodedData().write(to: secret)
try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: secret.path)
let publicFile = root.appendingPathComponent("public-key")
try key.publicKey.rawRepresentation.base64EncodedData().write(to: publicFile)
"""


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def exercise(case):
    bundle_id = "test.duckterm.updater." + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="duckterm-update-test-") as temporary:
        root = Path(temporary).resolve()

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root), **kwargs)

            def do_GET(self):
                # Never serve the disposable private key or unrelated temp files.
                if self.path not in ["/appcast.xml", "/update.zip"]:
                    self.send_error(404)
                    return
                super().do_GET()

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        base = f"http://127.0.0.1:{server.server_port}"
        app = root / "installed/DuckTerm Update Test.app"
        target = root / "target/DuckTerm Update Test.app"
        executable = app / "Contents/MacOS/UpdateProbe"
        key_source = root / "key.swift"
        key_source.write_text(KEY_SOURCE)
        run("swift", str(key_source), str(root))
        for directory in ["MacOS", "Resources", "Frameworks"]:
            (app / "Contents" / directory).mkdir(parents=True, exist_ok=True)
        run(
            "swiftc",
            "-target",
            "arm64-apple-macos13.0",
            "-F",
            str(FRAMEWORKS),
            "-framework",
            "AppKit",
            "-framework",
            "Sparkle",
            "-Xlinker",
            "-rpath",
            "-Xlinker",
            "@executable_path/../Frameworks",
            "-o",
            str(executable),
            str(ROOT / "mac/Tests/UpdateProbe/main.swift"),
            str(ROOT / "mac/Sources/Duckterm/AppUpdater.swift"),
            str(ROOT / "mac/Sources/Duckterm/AppUpdatePolicy.swift"),
        )
        run(
            "/usr/bin/ditto",
            str(FRAMEWORKS / "Sparkle.framework"),
            str(app / "Contents/Frameworks/Sparkle.framework"),
        )
        info = {
            "CFBundleIdentifier": bundle_id,
            "CFBundleExecutable": "UpdateProbe",
            "CFBundleName": "DuckTerm Update Test",
            "CFBundleVersion": "0.4.208",
            "CFBundleShortVersionString": "0.4.208",
            "CFBundlePackageType": "APPL",
            "LSMinimumSystemVersion": "13.0",
            "DucktermEdition": "full",
            "DucktermTestBuild": True,
            "DuckTermUpdateProbeRoot": str(root),
            "SUPublicEDKey": (root / "public-key").read_text(),
            "SUFeedURL": base + "/appcast.xml",
            "SUEnableAutomaticChecks": False,
            "SUAutomaticallyUpdate": False,
            "SUAllowsAutomaticUpdates": False,
            "SUVerifyUpdateBeforeExtraction": True,
            "SURequireSignedFeed": True,
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        (app / "Contents/Resources/backend-version").write_text("0.4.208")
        run("bash", str(ROOT / "mac/sign-bundle.sh"), str(app), "-")
        run("/usr/bin/ditto", str(app), str(target))
        info["CFBundleVersion"] = info["CFBundleShortVersionString"] = "0.4.209"
        (target / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        (target / "Contents/Resources/backend-version").write_text(
            "0.4.208" if case == "backend-mismatch" else "0.4.209"
        )
        run("bash", str(ROOT / "mac/sign-bundle.sh"), str(target), "-")
        archive = root / "update.zip"
        run("/usr/bin/ditto", "-c", "-k", "--keepParent", str(target), str(archive))
        signer = [str(SPARKLE / "bin/sign_update"), "--ed-key-file", str(root / "key")]
        signature = subprocess.check_output([*signer, "-p", str(archive)], text=True).strip()
        appcast = root / "appcast.xml"
        appcast.write_text(
            f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
<channel><title>Isolated test</title><item><title>0.4.209</title>
<sparkle:version>0.4.209</sparkle:version>
<enclosure url="{base}/update.zip" length="{archive.stat().st_size}"
 type="application/octet-stream" sparkle:edSignature="{signature}" />
</item></channel></rss>"""
        )
        run(*signer, str(appcast))
        if case == "corrupt-archive":
            data = bytearray(archive.read_bytes())
            data[len(data) // 2] ^= 1
            archive.write_bytes(data)
        if case == "corrupt-feed":
            appcast.write_bytes(appcast.read_bytes().replace(b"Isolated test", b"Altered test!"))
        sentinel = root / "saved-data"
        sentinel.write_text("Retain existing test data")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        opener = subprocess.Popen(["open", "-n", "-W", str(app)])
        try:
            for _ in range(120):
                time.sleep(1)
                try:
                    state = json.loads((root / "state.json").read_text())
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
                if state["phase"] not in ["succeeded", "failed"]:
                    continue
                installed = plistlib.loads((app / "Contents/Info.plist").read_bytes())[
                    "CFBundleVersion"
                ]
                if case in ["success", "backend-mismatch"]:
                    assert (root / "preflight").read_text() == "checked"
                    assert (root / "restart-ready").read_text() == "ready"
                assert sentinel.read_text() == "Retain existing test data"
                assert state["phase"] == ("succeeded" if case == "success" else "failed"), state
                assert installed == ("0.4.208" if case.startswith("corrupt") else "0.4.209"), state
                print(json.dumps({"case": case, "result": "passed", "state": state}), flush=True)
                break
            else:
                raise AssertionError("Native update test timed out")
        finally:
            server.shutdown()
            server.server_close()
            # Cleanup is restricted to this generated Test executable and its
            # unique preference domain. No broad process name or session kill.
            for line in subprocess.check_output(
                ["ps", "-axo", "pid=,command="], text=True
            ).splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2 and parts[1] == str(executable):
                    try:
                        import os

                        os.kill(int(parts[0]), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            try:
                opener.wait(timeout=5)
            except subprocess.TimeoutExpired:
                opener.terminate()
                opener.wait(timeout=5)
            subprocess.run(
                ["defaults", "delete", bundle_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=["success", "corrupt-archive", "corrupt-feed", "backend-mismatch", "all"],
        default="all",
    )
    args = parser.parse_args()
    for case in (
        ["success", "corrupt-archive", "corrupt-feed", "backend-mismatch"]
        if args.case == "all"
        else [args.case]
    ):
        exercise(case)


if __name__ == "__main__":
    main()
