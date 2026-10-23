"""Symbol index per commit for the Blast radius card (spec §8).

Python through `ast`: top-level functions, classes and methods, plus the
names each body references. TypeScript and TSX through a regex pass over
exported declarations and call-like identifiers. Name-based: no dynamic
dispatch, no string-keyed routes, and TypeScript resolution is by name only.
The card states those limits."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from duckterm.helpers import paths
from duckterm.review import buckets, git

MAX_FILES = 3000
LIMITS = (
    "Name-based: no dynamic dispatch, no string-keyed routes; "
    "TypeScript matched by identifier only."
)
MAX_BLOB = 400_000
PY_SUFFIXES = {".py"}
TS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}
SKIP_PARTS = {"node_modules", "dist", ".venv", "build", "__pycache__", ".git"}
TS_DEF = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|"
    r"(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\(|function\b|<))",
    re.MULTILINE,
)
TS_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
TS_IMPORT = re.compile(r"import\s*(?:type\s*)?\{([^}]*)\}")
PY_SKIP_NAMES = {"self", "cls", "print", "len", "str", "int", "dict", "list", "set", "isinstance"}


def indexable(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if any(part in SKIP_PARTS for part in parts):
        return False
    suffix = PurePosixPath(path).suffix.lower()
    return suffix in PY_SUFFIXES or suffix in TS_SUFFIXES


def blobs(repo: str, rev: str, files: list[str]) -> dict[str, str]:
    """File contents at a revision through one `git cat-file --batch`."""
    if not files:
        return {}
    request = "".join(f"{rev}:{path}\n" for path in files).encode()
    try:
        result = subprocess.run(
            ["git", "-C", repo, "cat-file", "--batch"],
            input=request,
            capture_output=True,
            timeout=120,
            env=git._env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out: dict[str, str] = {}
    data = result.stdout
    position = 0
    for path in files:
        newline = data.find(b"\n", position)
        if newline < 0:
            break
        header = data[position:newline].decode("utf-8", "replace").split()
        position = newline + 1
        if len(header) == 3 and header[1] == "blob":
            size = int(header[2])
            body = data[position : position + size]
            position += size + 1
            if size <= MAX_BLOB:
                out[path] = body.decode("utf-8", "replace")
    return out


def python_symbols(path: str, text: str) -> tuple[list[dict[str, Any]], set[str]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], set()
    defs: list[dict[str, Any]] = []
    refs: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            defs.append(
                {
                    "name": node.name,
                    "kind": "function",
                    "line": node.lineno,
                    "end": node.end_lineno or node.lineno,
                }
            )
        elif isinstance(node, ast.ClassDef):
            defs.append(
                {
                    "name": node.name,
                    "kind": "class",
                    "line": node.lineno,
                    "end": node.end_lineno or node.lineno,
                }
            )
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    defs.append(
                        {
                            "name": f"{node.name}.{item.name}",
                            "kind": "method",
                            "line": item.lineno,
                            "end": item.end_lineno or item.lineno,
                            "short": item.name,
                        }
                    )
    for walked in ast.walk(tree):
        if isinstance(walked, ast.Name):
            refs.add(walked.id)
        elif isinstance(walked, ast.Attribute):
            refs.add(walked.attr)
    return defs, refs - PY_SKIP_NAMES


def ts_symbols(path: str, text: str) -> tuple[list[dict[str, Any]], set[str]]:
    defs: list[dict[str, Any]] = []
    for match in TS_DEF.finditer(text):
        name = match[1] or match[2] or match[3]
        line = text.count("\n", 0, match.start()) + 1
        kind = "class" if match[2] else "function"
        defs.append({"name": name, "kind": kind, "line": line, "end": line})
    # A declaration runs to the next top-level declaration.
    for current, following in zip(defs, defs[1:] + [None], strict=False):
        last_line = text.count("\n") + (0 if text.endswith("\n") else 1)
        current["end"] = (following["line"] - 1) if following else last_line
    refs = set(TS_CALL.findall(text))
    for match in TS_IMPORT.finditer(text):
        for name in match[1].split(","):
            cleaned = name.strip().split(" as ")[-1].strip()
            if cleaned:
                refs.add(cleaned)
    return defs, refs


def build(repo: str, rev: str) -> dict[str, Any]:
    """Definitions and referenced names per file at `rev`."""
    try:
        listing = git.run(repo, "ls-tree", "-r", "--name-only", rev, timeout=60).splitlines()
    except git.ReviewGitError:
        listing = []
    files = [p for p in listing if indexable(p)][:MAX_FILES]
    contents = blobs(repo, rev, files)
    defs: dict[str, list[dict[str, Any]]] = {}
    refs: dict[str, list[str]] = {}
    for path, text in contents.items():
        suffix = PurePosixPath(path).suffix.lower()
        found, names = (
            python_symbols(path, text) if suffix in PY_SUFFIXES else ts_symbols(path, text)
        )
        if found:
            defs[path] = found
        if names:
            refs[path] = sorted(names)
    return {"rev": rev, "files": len(files), "defs": defs, "refs": refs}


def cache_dir(repo: str, slug: str | None) -> Path:
    from duckterm.review import store

    return paths.home() / "review" / store.repo_key(repo, slug) / "symbols"


def load_or_build(repo: str, slug: str | None, rev: str) -> dict[str, Any]:
    directory = cache_dir(repo, slug)
    path = directory / f"{rev}.json"
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("rev") == rev:
                return value
        except (ValueError, OSError):
            pass
    index = build(repo, rev)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index), encoding="utf-8")
    tmp.replace(path)
    return index


def _changed_lines(hunks: list[dict[str, Any]]) -> set[int]:
    """New-file line numbers that were added, plus the position of each
    deletion, so context lines around a change do not count."""
    lines: set[int] = set()
    for hunk in hunks:
        number = hunk["new_start"]
        for line in hunk["lines"]:
            if line["kind"] == "+":
                lines.add(number)
            elif line["kind"] == "-":
                lines.add(number)
                continue
            if line["kind"] != "\\":
                number += 1
    return lines


def _overlaps(definition: dict[str, Any], changed: set[int]) -> bool:
    return any(definition["line"] <= n <= definition["end"] for n in changed)


def blast_radius(
    repo: str, slug: str | None, base_sha: str, head_sha: str, files: list[dict[str, Any]]
) -> dict[str, Any]:
    """Changed symbols and who references them, inside and outside the diff."""
    base = load_or_build(repo, slug, base_sha)
    head = load_or_build(repo, slug, head_sha)
    changed_paths = {f["path"] for f in files}
    symbols: list[dict[str, Any]] = []
    for file in files:
        if not indexable(file["path"]):
            continue
        head_defs = head["defs"].get(file["path"], [])
        base_names = {d["name"] for d in base["defs"].get(file["path"], [])}
        changed = _changed_lines(file["hunks"])
        touched = [d for d in head_defs if file["status"] == "added" or _overlaps(d, changed)]
        # A class counts only when the change is outside its reported methods.
        method_classes = {d["name"].split(".")[0] for d in touched if d["kind"] == "method"}
        for definition in touched:
            if definition["kind"] == "class" and definition["name"] in method_classes:
                continue
            symbols.append(
                {
                    **definition,
                    "path": file["path"],
                    "change": "added" if definition["name"] not in base_names else "changed",
                }
            )
        head_names = {d["name"] for d in head_defs}
        for definition in base["defs"].get(file["path"], []):
            if definition["name"] not in head_names:
                symbols.append({**definition, "path": file["path"], "change": "removed"})
    rows: list[dict[str, Any]] = []
    edges: list[tuple[str, str]] = []
    for symbol in symbols:
        short = symbol.get("short") or symbol["name"].split(".")[-1]
        inside: list[str] = []
        outside: list[str] = []
        for path, names in head["refs"].items():
            if path == symbol["path"] or short not in names:
                continue
            (inside if path in changed_paths else outside).append(path)
        tested = any(buckets.is_test(p) for p in inside + outside)
        rows.append(
            {
                "symbol": symbol["name"],
                "kind": symbol["kind"],
                "path": symbol["path"],
                "line": symbol["line"],
                "change": symbol["change"],
                "callers_in_diff": sorted(inside)[:12],
                "callers_outside": sorted(outside)[:12],
                "outside_count": len(outside),
                "tested": tested,
            }
        )
        for caller in sorted(inside + outside)[:8]:
            edges.append((caller, f"{symbol['path']}::{symbol['name']}"))
    rows.sort(key=lambda r: (-r["outside_count"], r["path"], r["line"]))
    return {
        "symbols": rows[:60],
        "diagram": mermaid(rows[:20], edges),
        "index": {"base_files": base["files"], "head_files": head["files"]},
        "limits": LIMITS,
        "verdict": _verdict(rows),
    }


def _verdict(rows: list[dict[str, Any]]) -> str:
    outside = sum(1 for r in rows if r["outside_count"])
    untested = sum(1 for r in rows if not r["tested"])
    if not rows:
        return "No changed symbols"
    return f"{len(rows)} changed symbols · {outside} called outside the diff · {untested} untested"


def _node(label: str, ids: dict[str, str]) -> str:
    if label not in ids:
        ids[label] = f"n{len(ids)}"
    return ids[label]


def mermaid(rows: list[dict[str, Any]], edges: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    ids: dict[str, str] = {}
    lines = ["graph LR"]
    shown = {f"{r['path']}::{r['symbol']}" for r in rows}
    for row in rows:
        node = _node(f"{row['path']}::{row['symbol']}", ids)
        lines.append(f'  {node}["{row["symbol"]}"]')
    seen: set[tuple[str, str]] = set()
    for caller, target in edges:
        if target not in shown or (caller, target) in seen:
            continue
        seen.add((caller, target))
        lines.append(
            f'  {_node(caller, ids)}["{PurePosixPath(caller).name}"] --> {_node(target, ids)}'
        )
    return "\n".join(lines[:80])
