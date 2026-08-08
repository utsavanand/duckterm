#!/usr/bin/env python3
"""Rewrite the CURRENT-VERSIONS table in docs/releases.md from git tags, so the
release wiki never drifts from what's actually published. Run by release.sh
after a release; safe to run any time (idempotent)."""

import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "releases.md"
REPO_URL = "https://github.com/utsavanand/duckterm"
_PRE = re.compile(r"(a|b|rc)[0-9]+$")  # PEP 440 pre-release suffix on a tag


def _tags() -> list[str]:
    out = subprocess.run(
        ["git", "tag", "--list", "v*"], capture_output=True, text=True, check=True
    ).stdout
    return [t for t in out.split() if t]


def _sort_key(tag: str) -> list[int]:
    # Numeric sort on the version digits, so v0.10.0 > v0.2.0.
    nums = re.findall(r"\d+", tag)
    return [int(n) for n in nums]


def _latest(tags: list[str], *, prerelease: bool) -> str | None:
    matching = [t for t in tags if bool(_PRE.search(t)) == prerelease]
    return max(matching, key=_sort_key) if matching else None


def _row(label: str, tag: str | None) -> str:
    if tag is None:
        return f"| {label} | _(none yet)_ | — |"
    return f"| {label} | `{tag}` | {REPO_URL}/releases/tag/{tag} |"


def main() -> int:
    tags = _tags()
    prod = _latest(tags, prerelease=False)
    beta = _latest(tags, prerelease=True)
    table = "\n".join(
        [
            "| Tier | Version | Install from |",
            "|---|---|---|",
            _row("**prod** (stable, daily)", prod),
            _row("**beta** (staging, testing)", beta),
            "| **dev** (source) | `main` | git checkout |",
        ]
    )
    text = DOC.read_text()
    new = re.sub(
        r"(<!-- CURRENT-VERSIONS:START.*?-->\n).*?(<!-- CURRENT-VERSIONS:END -->)",
        rf"\1{table}\n\2",
        text,
        flags=re.DOTALL,
    )
    if new == text:
        print("stamp: no change")
        return 0
    DOC.write_text(new)
    print(f"stamp: prod={prod} beta={beta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
