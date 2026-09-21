#!/usr/bin/env python3
"""Refuse publication unless this clean main checkout has passed its push CI."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def output(*args: str) -> str:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"{args[0]} failed")
    return result.stdout.strip()


def verify(expected_commit: str | None = None) -> str:
    if output("git", "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("release requires a clean checkout; commit or remove pending changes")
    if output("git", "branch", "--show-current") != "main":
        raise RuntimeError("release requires the main branch")
    commit = output("git", "rev-parse", "HEAD")
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeError("HEAD changed during the build; refusing to publish different source")
    runs = json.loads(
        output(
            "gh",
            "run",
            "list",
            "--workflow",
            "ci.yml",
            "--commit",
            commit,
            "--branch",
            "main",
            "--event",
            "push",
            "--limit",
            "1",
            "--json",
            "headSha,headBranch,event,status,conclusion,url",
        )
    )
    if not isinstance(runs, list) or not runs or not isinstance(runs[0], dict):
        raise RuntimeError("no main push CI run found for HEAD; push it and wait for CI")
    run = runs[0]
    if (
        run.get("headSha") != commit
        or run.get("headBranch") != "main"
        or run.get("event") != "push"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise RuntimeError(f"HEAD has not passed main push CI: {run.get('url', 'unknown run')}")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    try:
        print(verify(args.expected_commit))
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"release blocked: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
