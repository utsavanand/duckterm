#!/usr/bin/env python3
"""Stamp public update configuration. Private signing material never enters bundles."""
import argparse
import base64
import os
from pathlib import Path
import plistlib
from urllib.parse import urlsplit


def configure(info, public_key, test_feed=None):
    test = info.get("DucktermTestBuild") is True
    edition = info["DucktermEdition"]
    if not public_key:
        if test_feed:
            raise ValueError("A test feed requires its own public key")
        return info  # Source/test builds can run with updating unavailable.
    if len(base64.b64decode(public_key, validate=True)) != 32:
        raise ValueError("The Sparkle public key must be 32 bytes")
    if edition not in {"lite", "code", "full"}:
        raise ValueError("Unknown edition")
    if test:
        parsed = urlsplit(test_feed or "")
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Test apps require a separate loopback update feed")
        feed = test_feed
    else:
        if test_feed:
            raise ValueError("A production app cannot use a test feed")
        feed = f"https://github.com/utsavanand/duckterm/releases/latest/download/appcast-{edition}-arm64.xml"
    return {
        **info,
        "SUFeedURL": feed,
        "SUPublicEDKey": public_key,
        "SUEnableAutomaticChecks": False,
        "SUAutomaticallyUpdate": False,
        "SUEnableSystemProfiling": False,
        "SUShowReleaseNotes": False,
        "SUVerifyUpdateBeforeExtraction": True,
        "SURequireSignedFeed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plist", type=Path)
    parser.add_argument(
        "--require", action="store_true", help="Fail a distribution build without signed updates"
    )
    args = parser.parse_args()
    info = plistlib.loads(args.plist.read_bytes())
    if args.require:
        key = info.get("SUPublicEDKey", "")
        expected = configure(info, key)
        if not key or any(info.get(k) != v for k, v in expected.items()):
            raise ValueError(
                "Distribution requires the approved public key and edition-specific signed feed"
            )
        if info.get("DucktermTestBuild"):
            raise ValueError("Test apps are not distribution updates")
    else:
        info = configure(
            info,
            os.environ.get("DUCKTERM_UPDATE_PUBLIC_KEY", ""),
            os.environ.get("DUCKTERM_TEST_UPDATE_FEED"),
        )
        args.plist.write_bytes(plistlib.dumps(info))


if __name__ == "__main__":
    main()
