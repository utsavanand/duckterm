"""Risk buckets for changed files (docs/pr-review-platform-spec.md §4).

Deterministic and model-free. A file lands in the highest bucket any rule puts
it in; the rules that fired travel with the file so the reviewer can disagree.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from duckterm.review.diff import FileDiff

HIGH, READ, SKIM = "high", "read", "skim"
ORDER = {HIGH: 0, READ: 1, SKIM: 2}

SCHEMA_LINE = re.compile(r"\b(CREATE|ALTER|DROP)\s+TABLE\b|\bmigrat", re.IGNORECASE)
LOCK_LINE = re.compile(r"\bLock\(|with self\._lock|asyncio\.Lock|threading\.")
ROUTE_LINE = re.compile(
    r"\bRoute\(|@app\.route|add_route\(|_require_owner|authHeaders\(|token_valid\(|re\.fullmatch\(r\"/"
)
SOURCE = {".py", ".ts", ".tsx", ".js", ".jsx", ".swift", ".go", ".rs", ".sh", ".css", ".html"}
DOCS = {".md", ".rst", ".txt"}
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".icns"}
LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "Cargo.lock",
}
VERSION_LINE = re.compile(r"^\s*\"?version\"?\s*[:=]\s*\"?[0-9][^\"]*\"?,?\s*$")
STRING = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`")
HOTSPOT_COMMITS = 10
LARGE_NEW_LINES = 100


def is_test(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = PurePosixPath(path).name
    return (
        "tests" in parts
        or "test" in parts
        or "e2e" in parts
        or ".test." in name
        or ".spec." in name
        or name.startswith("test_")
    )


def _strings_only(file: FileDiff) -> bool:
    removed, added = file.removed_lines, file.added_lines
    if not removed or len(removed) != len(added):
        return False
    for old, new in zip(removed, added, strict=True):
        if old == new:
            return False
        if STRING.sub('""', old).strip() != STRING.sub('""', new).strip():
            return False
        if not STRING.search(old) and not STRING.search(new):
            return False
    return True


def classify(
    file: FileDiff, *, whitespace_only: bool, hotspot_commits: int = 0
) -> tuple[str, list[dict[str, str]]]:
    path = file.path
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix.lower()
    parts = PurePosixPath(path).parts
    added = file.added_lines
    rules: list[dict[str, str]] = []

    def fire(bucket: str, rule: str, reason: str) -> None:
        rules.append({"bucket": bucket, "id": rule, "reason": reason})

    if (
        "persistence" in parts
        or "migrations" in parts
        or any(SCHEMA_LINE.search(line) for line in added)
    ):
        fire(HIGH, "schema", "Touches persistence or a schema statement")
    if any(LOCK_LINE.search(line) for line in added):
        fire(HIGH, "locks", "Adds or changes locking")
    if any(ROUTE_LINE.search(line) for line in added):
        fire(HIGH, "routes", "Adds or changes an HTTP route or an auth check")
    if (parts[:1] in (("scripts",), ("mac",)) and suffix != ".md") or name.startswith("install"):
        fire(HIGH, "ops", "Build, packaging or installer path")
    if ".github" in parts and "workflows" in parts:
        fire(HIGH, "ops", "CI workflow")
    if file.status == "added" and file.additions > LARGE_NEW_LINES and not is_test(path):
        fire(HIGH, "large-new", f"New file with {file.additions} lines")
    if hotspot_commits > HOTSPOT_COMMITS:
        fire(HIGH, "hotspot", f"Changed in {hotspot_commits} commits in the last 30 days")
    if is_test(path):
        fire(READ, "tests", "Test file")
    elif suffix in SOURCE:
        fire(READ, "source", "Source file")
    if suffix in DOCS:
        fire(SKIM, "docs", "Documentation")
    if name in LOCKFILES:
        fire(SKIM, "lockfile", "Lockfile")
    if suffix in IMAGES or file.binary:
        fire(SKIM, "binary", "Binary or image")
    if (
        name in ("package.json", "pyproject.toml")
        and added
        and all(VERSION_LINE.match(line) for line in added)
    ):
        fire(SKIM, "version-bump", "Only the version changed")

    if file.status == "renamed" and not file.hunks:
        fire(SKIM, "rename-only", "Renamed without content changes")
        return SKIM, rules
    if whitespace_only and file.status == "modified":
        fire(SKIM, "whitespace-only", "Only whitespace changed")
        return SKIM, rules
    if not file.binary and _strings_only(file):
        fire(SKIM, "strings-only", "Only string literals changed")
        if not any(r["bucket"] == HIGH for r in rules):
            return SKIM, rules
    if not rules:
        fire(READ, "default", "No rule matched; read it")
    return min((r["bucket"] for r in rules), key=lambda b: ORDER[b]), rules
