"""Exercise the production plist-writing steps without building or sending mail."""

import os
import plistlib
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("edition", ["lite", "code", "full"])
@pytest.mark.parametrize("override", [None, "", "support@example.test"])
def test_packaged_report_recipient_without_local_preferences(tmp_path, edition, override):
    build = (Path(__file__).resolve().parents[2] / "mac/build.sh").read_text()
    # Execute the actual production shell blocks, including XML expansion and
    # the optional environment override, in an empty fake bundle directory.
    plist_start = build.index('cat > "$CONTENTS/Info.plist" <<PLIST')
    plist_end = build.index("\nPLIST\n", plist_start) + len("\nPLIST\n")
    override_start = build.index('if [[ -n "${DUCKTERM_SUPPORT_EMAIL:-}" ]]')
    override_end = build.index("\nfi\n", override_start) + len("\nfi\n")
    env = {
        **os.environ,
        "CONTENTS": str(tmp_path),
        "APP_NAME": f"DuckTerm {edition.title()}",
        "EDITION": edition,
        "BUNDLE_ID": f"test.duckterm.{edition}",
        "VERSION": "0.0.0",
        "MIN_MACOS": "13.0",
    }
    env.pop("DUCKTERM_SUPPORT_EMAIL", None)
    if override is not None:
        env["DUCKTERM_SUPPORT_EMAIL"] = override
    subprocess.run(
        [
            "/bin/bash",
            "-eu",
            "-c",
            build[plist_start:plist_end] + build[override_start:override_end],
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    info = plistlib.loads((tmp_path / "Info.plist").read_bytes())
    assert info["DucktermEdition"] == edition
    assert info["DuckTermSupportEmail"] == (override or "getutsava@gmail.com")
