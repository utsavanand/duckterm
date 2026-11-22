#!/usr/bin/env python3
"""Build signed, edition-specific Sparkle feeds from the three final Mac ZIPs.

Run after signing, notarizing and stapling the apps. This never publishes a
release or creates/exports a signing key. Upload all three feeds with the ZIPs
before making the GitHub release latest.
"""
import argparse
import base64
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
ET.register_namespace("sparkle", SPARKLE)
RELEASES = "https://github.com/utsavanand/duckterm/releases"


def archive_name(edition, version):
    name = f"DuckTerm-{edition.title()}"
    return f"{name}-{version}-macos-arm64.zip"


def validate_info(info, edition, version):
    expected_feed = f"{RELEASES}/latest/download/appcast-{edition}-arm64.xml"
    if (
        info.get("DucktermEdition") != edition
        or info.get("DucktermTestBuild")
        or info.get("CFBundleVersion") != version
        or info.get("CFBundleShortVersionString") != version
        or info.get("SUFeedURL") != expected_feed
        or info.get("SUVerifyUpdateBeforeExtraction") is not True
        or info.get("SURequireSignedFeed") is not True
        or info.get("SUEnableAutomaticChecks") is not False
        or info.get("SUAutomaticallyUpdate") is not False
    ):
        raise ValueError("Archive version, edition or signed update configuration is incorrect")
    key = info.get("SUPublicEDKey", "")
    if len(base64.b64decode(key, validate=True)) != 32:
        raise ValueError("Archive does not contain an update public key")
    return key


def feed_xml(edition, version, length, signature, minimum_os):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Only final release versions are supported")
    if len(base64.b64decode(signature, validate=True)) != 64:
        raise ValueError("Missing EdDSA archive signature")
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = f"DuckTerm {edition.title()}"
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = f"DuckTerm {version}"
    ET.SubElement(item, f"{{{SPARKLE}}}version").text = version
    ET.SubElement(item, f"{{{SPARKLE}}}shortVersionString").text = version
    ET.SubElement(item, f"{{{SPARKLE}}}minimumSystemVersion").text = minimum_os
    ET.SubElement(
        item,
        "enclosure",
        {
            "url": f"{RELEASES}/download/v{version}/{archive_name(edition, version)}",
            "length": str(length),
            "type": "application/octet-stream",
            f"{{{SPARKLE}}}edSignature": signature,
            f"{{{SPARKLE}}}os": "macos",
            f"{{{SPARKLE}}}cpuArchitecture": "arm64",
        },
    )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sign-tool", type=Path, required=True)
    parser.add_argument(
        "--account",
        default="duckterm-updates",
        help="Existing release signing key’s Keychain account",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Output directory must not exist; previous feeds are never overwritten")
    signer = [str(args.sign_tool.resolve()), "--account", args.account]
    feeds = {}
    keys = set()
    with tempfile.TemporaryDirectory(prefix="duckterm-appcast-") as tmp:
        for edition in ("lite", "code", "full"):
            archive = args.archives.resolve() / archive_name(edition, args.version)
            with zipfile.ZipFile(archive) as package:
                infos = [
                    n
                    for n in package.namelist()
                    if n.count("/") == 2 and n.endswith(".app/Contents/Info.plist")
                ]
                if len(infos) != 1:
                    raise ValueError(f"Expected one complete app in {archive.name}")
                info = plistlib.loads(package.read(infos[0]))
                keys.add(validate_info(info, edition, args.version))
            extracted = Path(tmp) / edition
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(extracted)], check=True)
            app = extracted / infos[0].split("/")[0]
            subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True
            )
            # Gatekeeper checks the final notarized bundle, not a development app.
            subprocess.run(
                ["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)], check=True
            )
            signature = subprocess.check_output([*signer, "-p", str(archive)], text=True).strip()
            subprocess.run(
                [
                    "swift",
                    str(Path(__file__).resolve().parents[1] / "mac/verify-update-signature.swift"),
                    info["SUPublicEDKey"],
                    signature,
                    str(archive),
                ],
                check=True,
            )
            path = Path(tmp) / f"appcast-{edition}-arm64.xml"
            path.write_bytes(
                feed_xml(
                    edition,
                    args.version,
                    archive.stat().st_size,
                    signature,
                    info["LSMinimumSystemVersion"],
                )
            )
            subprocess.run([*signer, str(path)], check=True)
            subprocess.run([*signer, "--verify", str(path)], check=True)
            feeds[path.name] = path.read_bytes()
        if len(keys) != 1:
            raise ValueError("All three editions must use the same approved update public key")
        output.mkdir(parents=True)
        for name, data in feeds.items():
            target = output / name
            target.write_bytes(data)
            print(target)


if __name__ == "__main__":
    main()
