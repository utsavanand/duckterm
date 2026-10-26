"""Deterministic inputs for the Tests and Guardrails cards (spec §5, cards 6
and 7): slop regexes, commit-message and trailer checks, stdlib-only imports,
retired terms, piped gates, existing tests per changed module, and the last
gate run when one is on disk. No model call in this module."""

from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any

from duckterm.review import buckets, git

GATE_LOG = Path("/tmp/duckterm-gate.log")
RETIRED = re.compile(r"\b(meta-harness(?:es)?|overlays?|suites?)\b", re.IGNORECASE)
RETIRED_ALLOWED = re.compile(r"\btest suites?\b", re.IGNORECASE)
TRAILER = re.compile(r"^\s*(Co-Authored-By|Signed-off-by|Generated with)\b", re.IGNORECASE)
PIPED_GATE = re.compile(r"(gate\.sh|pytest|vitest|playwright)[^\n|]*\|\s*(grep|tail|head)\b")
GIT_ADD_ALL = re.compile(r"\bgit add (-A|--all|\.)\b")
IMPORT = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))")
SLOP_TS = [
    ("toBeTruthy", re.compile(r"\.toBeTruthy\(\)")),
    ("toBeDefined", re.compile(r"\.toBeDefined\(\)")),
    ("snapshot", re.compile(r"\.toMatchSnapshot\(\)")),
]
SLOP_PY = [
    ("assert True", re.compile(r"^\s*assert True\b")),
    ("assert not None", re.compile(r"^\s*assert \w+ is not None\s*$")),
]
TEST_OPEN_TS = re.compile(r"^\s*(?:it|test)\(\s*['\"`]")
TEST_OPEN_PY = re.compile(r"^\s*(?:async\s+)?def (test_\w+)\(")


def test_slop(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assertion-free tests and existence-only matchers in added test lines."""
    findings: list[dict[str, Any]] = []
    for file in files:
        if not buckets.is_test(file["path"]) or file["binary"]:
            continue
        ts = file["path"].endswith((".ts", ".tsx", ".js", ".jsx"))
        patterns = SLOP_TS if ts else SLOP_PY
        opener = TEST_OPEN_TS if ts else TEST_OPEN_PY
        for hunk in file["hunks"]:
            added = [
                (hunk["new_start"] + i, line)
                for i, line in enumerate(hunk["lines"])
                if line["kind"] != "-"
            ]
            numbered = [(n, line["text"]) for n, line in added if line["kind"] == "+"]
            for number, text in numbered:
                for kind, pattern in patterns:
                    if pattern.search(text):
                        findings.append(
                            {
                                "path": file["path"],
                                "line": number,
                                "kind": kind,
                                "text": text.strip(),
                            }
                        )
            # A test body added in full with no assertion at all.
            block: list[tuple[int, str]] = []
            for number, line in added:
                text = line["text"]
                if opener.match(text):
                    if block:
                        findings.extend(_assertion_free(file["path"], block))
                    block = [(number, text)] if line["kind"] == "+" else []
                elif block:
                    block.append((number, text))
            if block:
                findings.extend(_assertion_free(file["path"], block))
    return findings


def _assertion_free(path: str, block: list[tuple[int, str]]) -> list[dict[str, Any]]:
    body = "\n".join(text for _, text in block)
    if re.search(r"\bexpect\(|\bassert\b|pytest\.raises|toThrow|\.rejects\b", body):
        return []
    return [
        {"path": path, "line": block[0][0], "kind": "no assertion", "text": block[0][1].strip()}
    ]


def commit_messages(commits: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The repository convention: problem / fix / how-tested in the subject,
    and no attribution trailers."""
    findings: list[dict[str, Any]] = []
    for commit in commits:
        subject = commit["subject"]
        if subject.lower().startswith("merge "):
            continue
        if subject.count(" / ") < 2:
            findings.append({"sha": commit["sha"][:7], "kind": "format", "text": subject[:120]})
        for line in commit["body"].splitlines():
            if TRAILER.match(line):
                findings.append(
                    {"sha": commit["sha"][:7], "kind": "trailer", "text": line.strip()[:120]}
                )
    return findings


def _stdlib() -> set[str]:
    return set(getattr(sys, "stdlib_module_names", set()))


def stdlib_imports(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """New imports under src/ that are neither stdlib nor the package itself."""
    allowed = _stdlib() | {"duckterm"}
    findings: list[dict[str, Any]] = []
    for file in files:
        parts = PurePosixPath(file["path"]).parts
        if (
            not file["path"].endswith(".py")
            or "src" not in parts[:1]
            or buckets.is_test(file["path"])
        ):
            continue
        for hunk in file["hunks"]:
            number = hunk["new_start"]
            for line in hunk["lines"]:
                if line["kind"] == "+":
                    match = IMPORT.match(line["text"])
                    if match:
                        module = (match[1] or match[2]).split(".")[0]
                        if module not in allowed and not module.startswith("_"):
                            findings.append(
                                {
                                    "path": file["path"],
                                    "line": number,
                                    "kind": "dependency",
                                    "text": line["text"].strip(),
                                }
                            )
                if line["kind"] != "-":
                    number += 1
    return findings


def retired_terms(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retired product terms in added documentation or UI lines."""
    findings: list[dict[str, Any]] = []
    for file in files:
        path = file["path"]
        if not (path.endswith(".md") or path.startswith("web/src/")) or buckets.is_test(path):
            continue
        for hunk in file["hunks"]:
            number = hunk["new_start"]
            for line in hunk["lines"]:
                if line["kind"] == "+":
                    text = RETIRED_ALLOWED.sub("", line["text"])
                    match = RETIRED.search(text)
                    if match:
                        findings.append(
                            {"path": path, "line": number, "kind": "retired term", "text": match[0]}
                        )
                if line["kind"] != "-":
                    number += 1
    return findings


def shell_habits(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gates piped through a filter, and whole-tree staging, in added lines."""
    findings: list[dict[str, Any]] = []
    for file in files:
        if file["binary"]:
            continue
        for hunk in file["hunks"]:
            number = hunk["new_start"]
            for line in hunk["lines"]:
                if line["kind"] == "+":
                    if PIPED_GATE.search(line["text"]):
                        findings.append(
                            {
                                "path": file["path"],
                                "line": number,
                                "kind": "piped gate",
                                "text": line["text"].strip()[:120],
                            }
                        )
                    if GIT_ADD_ALL.search(line["text"]):
                        findings.append(
                            {
                                "path": file["path"],
                                "line": number,
                                "kind": "git add -A",
                                "text": line["text"].strip()[:120],
                            }
                        )
                if line["kind"] != "-":
                    number += 1
    return findings


def docs_only(paths: list[str]) -> bool:
    return bool(paths) and all(p.endswith((".md", ".txt", ".rst")) for p in paths)


def existing_tests(repo: str, head_ref: str, paths: list[str]) -> dict[str, list[str]]:
    """Test files at head whose name refers to each changed module."""
    try:
        tree = git.run(repo, "ls-tree", "-r", "--name-only", head_ref).splitlines()
    except git.ReviewGitError:
        return {}
    tests = [p for p in tree if buckets.is_test(p)]
    out: dict[str, list[str]] = {}
    for path in paths:
        if buckets.is_test(path) or PurePosixPath(path).suffix.lower() not in buckets.SOURCE:
            continue
        stem = PurePosixPath(path).stem
        if stem in ("__init__", "index"):
            stem = PurePosixPath(path).parent.name
        if len(stem) < 2:
            continue
        matches = [t for t in tests if stem.lower() in PurePosixPath(t).name.lower()]
        out[path] = sorted(matches)[:8]
    return out


def gate_report(head_time: int | None) -> dict[str, Any] | None:
    """The last local gate run, if its log is newer than the head commit."""
    try:
        stat = GATE_LOG.stat()
    except OSError:
        return None
    if head_time and stat.st_mtime < head_time:
        return None
    try:
        text = GATE_LOG.read_text(errors="replace")[-20000:]
    except OSError:
        return None
    summary = re.findall(r"(\d+ passed[^\n]*)", text)
    return {
        "at": int(stat.st_mtime),
        "age_minutes": int((time.time() - stat.st_mtime) / 60),
        "passed": "GATE PASSED" in text,
        "summary": summary[-1][:120] if summary else None,
    }


def coverage_report(repo: str, paths: list[str]) -> dict[str, Any] | None:
    """Per-file line coverage for changed files from a report on disk:
    coverage.xml (pytest-cov) or web/coverage/coverage-summary.json (vitest)."""
    rows: dict[str, float] = {}
    xml_path = Path(repo) / "coverage.xml"
    if xml_path.exists():
        try:
            root = ET.parse(xml_path).getroot()
            for cls in root.iter("class"):
                name = str(cls.get("filename") or "")
                rate = float(cls.get("line-rate") or 0)
                for path in paths:
                    if path.endswith(name):
                        rows[path] = round(rate * 100)
        except (ET.ParseError, ValueError, OSError):
            pass
    summary = Path(repo) / "web" / "coverage" / "coverage-summary.json"
    if summary.exists():
        try:
            data = json.loads(summary.read_text())
            for name, value in data.items():
                for path in paths:
                    if name != "total" and str(name).endswith(path.removeprefix("web/")):
                        rows[path] = float(value["lines"]["pct"])
        except (ValueError, OSError, KeyError, TypeError):
            pass
    if not rows:
        return None
    return {
        "files": rows,
        "source": "coverage.xml" if xml_path.exists() else "web/coverage/coverage-summary.json",
    }


def rules_text(repo: str, head_ref: str) -> dict[str, str]:
    """AGENTS.md and .claude/rules/*.md at head, for the model's slop pass."""
    out: dict[str, str] = {}
    for path in ("AGENTS.md", "CLAUDE.md"):
        try:
            out[path] = git.run(repo, "show", f"{head_ref}:{path}")[:20000]
        except git.ReviewGitError:
            continue
    try:
        listing = git.run(repo, "ls-tree", "--name-only", head_ref, ".claude/rules/").splitlines()
    except git.ReviewGitError:
        listing = []
    for path in listing:
        if path.endswith(".md"):
            try:
                out[path] = git.run(repo, "show", f"{head_ref}:{path}")[:8000]
            except git.ReviewGitError:
                continue
    return out
