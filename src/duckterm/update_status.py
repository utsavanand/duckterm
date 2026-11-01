"""Read-only release information. No installer is enabled by this endpoint."""

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from duckterm import __version__, build_info
from duckterm.helpers import instance

RELEASES = "https://github.com/utsavanand/duckterm/releases"
REASON = "In-app installation is not available yet. Download the release to update manually."


def status() -> dict[str, Any]:
    profile = instance.configured_profile()
    result: dict[str, Any] = {
        "installed_version": __version__,
        **build_info.installed(),
        "latest_version": None,
        "release_url": RELEASES,
        "update_available": None,
        "install_available": False,
        "reason": REASON,
        "check_status": "failed",
        "check_error": None,
        "operation": None,
    }
    if profile is not None:
        result.update(edition=profile["edition"], profile=profile["profile_id"])
        if profile["edition"] in {"code", "full"}:
            result.update(
                release_url="https://duckterm.utsava.xyz/",
                check_status="unavailable",
                reason=f"Duckterm {profile['edition'].title()} updates use your invite-only "
                "download. Replace only this edition's app; its workspace stays in place.",
            )
            return result
        result.update(
            release_url="https://duckterm.utsava.xyz/",
            reason="Download Duckterm Lite and replace DuckTerm Lite.app. "
            "Its workspace stays in place; other editions keep their own apps and data.",
        )
    if os.environ.get("DUCKTERM_RELEASE_CHECK") == "off":
        result["check_error"] = "Release checks are disabled in this test instance."
        return result
    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/utsavanand/duckterm/releases/latest",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "DuckTerm-release-check",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = response.read(1_048_577)
        if len(payload) > 1_048_576:
            raise ValueError("Release response is too large")
        release = json.loads(payload)
        if not isinstance(release, dict) or release.get("prerelease") or release.get("draft"):
            raise ValueError("No stable release information received")
        tag = release.get("tag_name")
        if not isinstance(tag, str):
            raise ValueError("The release version could not be read")
        latest = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag)
        if latest is None:
            raise ValueError("The release version could not be read")
        installed = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", __version__)
        result.update(
            check_status="checked",
            latest_version=tag.removeprefix("v"),
            release_url=RELEASES + "/tag/" + tag,
            update_available=(
                (tuple(map(int, latest.groups())) > tuple(map(int, installed.groups())))
                if installed and result["installed_is_release"] is True
                else None
            ),
        )
        if profile is not None:
            # A new source wheel doesn't prove a matching native app exists.
            asset = f"DuckTerm-Lite-{tag.removeprefix('v')}-macos-arm64.zip"
            assets = release.get("assets", [])
            available = isinstance(assets, list) and any(
                isinstance(item, dict) and item.get("name") == asset for item in assets
            )
            result["release_url"] = (
                RELEASES + "/download/" + tag + "/" + asset
                if available
                else "https://duckterm.utsava.xyz/"
            )
            if not available:
                result.update(
                    latest_version=None,
                    update_available=None,
                    reason="A matching Duckterm Lite app is not available in the latest release. "
                    "Use the verified Lite download on the website.",
                )
    except (OSError, ValueError, urllib.error.URLError) as exc:
        result["check_error"] = str(exc)
    return result
