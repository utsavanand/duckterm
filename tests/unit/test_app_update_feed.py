import base64
import runpy
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
configure = runpy.run_path(str(ROOT / "mac/configure-updates.py"))["configure"]
feed = runpy.run_path(str(ROOT / "scripts/build_update_feeds.py"))
KEY = base64.b64encode(bytes(32)).decode()
SIG = base64.b64encode(bytes(64)).decode()


@pytest.mark.parametrize("edition", ["lite", "code", "full"])
def test_feed_matches_complete_archive_and_signed_configuration(edition):
    info = configure(
        {
            "DucktermEdition": edition,
            "CFBundleVersion": "0.4.210",
            "CFBundleShortVersionString": "0.4.210",
        },
        KEY,
    )
    assert feed["validate_info"](info, edition, "0.4.210") == KEY
    root = ET.fromstring(feed["feed_xml"](edition, "0.4.210", 1234, SIG, "13.0"))
    enclosure = root.find("./channel/item/enclosure")
    assert enclosure is not None
    assert enclosure.attrib["url"].endswith(feed["archive_name"](edition, "0.4.210"))
    assert enclosure.attrib["length"] == "1234"
    assert enclosure.attrib[f"{{{feed['SPARKLE']}}}edSignature"] == SIG


def test_test_apps_require_isolated_feed_and_production_cannot_use_it():
    test = {"DucktermEdition": "full", "DucktermTestBuild": True}
    with pytest.raises(ValueError):
        configure(test, KEY)
    with pytest.raises(ValueError):
        configure({"DucktermEdition": "full"}, KEY, "http://127.0.0.1:59240/feed.xml")
    assert configure(test, KEY, "http://127.0.0.1:59240/feed.xml")["SURequireSignedFeed"] is True


@pytest.mark.parametrize(
    "change",
    [
        {"DucktermTestBuild": True},
        {"DucktermEdition": "lite"},
        {"SURequireSignedFeed": False},
        {"SUPublicEDKey": ""},
        {"SUFeedURL": "https://evil.test/feed.xml"},
        {"CFBundleVersion": "0.4.209"},
    ],
)
def test_distribution_rejects_wrong_or_unsigned_app(change):
    info = configure(
        {
            "DucktermEdition": "full",
            "CFBundleVersion": "0.4.210",
            "CFBundleShortVersionString": "0.4.210",
        },
        KEY,
    )
    with pytest.raises(ValueError):
        feed["validate_info"]({**info, **change}, "full", "0.4.210")
