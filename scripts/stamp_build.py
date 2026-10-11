#!/usr/bin/env python3
"""Stamp Git facts into the package without importing DuckTerm or querying a remote."""

import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def collect(root: Path, version: str) -> dict[str, Any]:
    values: dict[str, Any] = dict.fromkeys(
        ("COMMIT", "DESCRIBE", "BRANCH", "DIRTY", "IN_MAIN", "TAG", "IS_RELEASE")
    )
    values["VERSION"] = version

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10
        )

    try:
        commit = git("rev-parse", "--verify", "HEAD")
        if commit.returncode or not re.fullmatch(
            r"[a-f0-9]{40}(?:[a-f0-9]{24})?", commit.stdout.strip()
        ):
            return values
        values["COMMIT"] = commit.stdout.strip()
        describe = git("describe", "--tags", "--always", "--dirty")
        if describe.returncode == 0:
            values["DESCRIBE"] = describe.stdout.strip()
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
        if branch.returncode == 0:
            values["BRANCH"] = branch.stdout.strip()
        status = git("status", "--porcelain", "--untracked-files=normal")
        if status.returncode == 0:
            values["DIRTY"] = bool(status.stdout)
        contained = git("merge-base", "--is-ancestor", values["COMMIT"], "origin/main")
        if contained.returncode in (0, 1):
            values["IN_MAIN"] = contained.returncode == 0
        tag = git("rev-parse", "--verify", f"refs/tags/v{version}^{{commit}}")
        exact_tag = tag.returncode == 0 and tag.stdout.strip() == values["COMMIT"]
        values["TAG"] = f"v{version}" if exact_tag else None
        if values["DIRTY"] is True or values["IN_MAIN"] is False or not exact_tag:
            values["IS_RELEASE"] = False
        elif values["DIRTY"] is False and values["IN_MAIN"] is True:
            values["IS_RELEASE"] = True
    except (OSError, subprocess.SubprocessError):
        # Build identity is unknown when the Git observation could not finish.
        return {**dict.fromkeys(values), "VERSION": version}
    return values


def stamp(root: Path, version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)", version):
        raise ValueError("Expected the sed-readable package version")
    values = collect(root, version)
    target = root / "src" / "duckterm" / "_build.py"
    # Never overwrite a tracked file, peer work, or a stale generated stamp.
    with target.open("x", encoding="utf-8") as stream:
        stream.write('"""Generated build identity; do not edit or commit."""\n\n')
        for name, value in values.items():
            stream.write(f"{name} = {value!r}\n")


if __name__ == "__main__":
    stamp(Path.cwd(), sys.argv[1])
