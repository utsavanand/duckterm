"""The History tab (spec §7): one node per release tag with the architecture
at that release, and the difference between any two.

Releases come from `git tag --sort=creatordate` matching v*. A module map per
tag is one llm.backend call over the import graph at that tag (Python
imports through ast, TypeScript import statements), cached as JSON. Maps are
generated lazily, newest first, so the tab is usable within seconds."""

from __future__ import annotations

import ast
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from duckterm.core.session_api import APIError
from duckterm.llm import backend
from duckterm.review import cards, git, store, symbols

TAG = re.compile(r"^v\d")
TS_IMPORT = re.compile(r"""^\s*import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]""", re.MULTILINE)
LANES = ("Clients", "Server", "Runtime")
MAX_MODULES = 40
MAP_FILE = "archmap"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["modules", "edges"],
    "properties": {
        "modules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "name", "lane", "files", "what"],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "lane": {"type": "string", "enum": list(LANES)},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "what": {"type": "string"},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
        },
    },
}


def releases(repo: str) -> list[dict[str, Any]]:
    """Release tags oldest first with date, subject and merges since the
    previous tag."""
    try:
        out = git.run(
            repo,
            "tag",
            "--list",
            "v*",
            "--sort=creatordate",
            "--format=%(refname:short)%00%(creatordate:unix)%00%(objectname)%00%(subject)",
        )
    except git.ReviewGitError:
        return []
    rows: list[dict[str, Any]] = []
    previous: str | None = None
    for line in out.splitlines():
        parts = line.split("\x00")
        if len(parts) != 4 or not TAG.match(parts[0]):
            continue
        tag, when, sha, subject = parts
        merges = _merges(repo, previous, tag)
        rows.append(
            {
                "tag": tag,
                "at": int(when or 0),
                "sha": git.resolve(repo, tag) or sha,
                "subject": subject.strip()[:160],
                "merges": merges,
                "previous": previous,
            }
        )
        previous = tag
    return rows


def _merges(repo: str, previous: str | None, tag: str) -> list[dict[str, Any]]:
    span = f"{previous}..{tag}" if previous else tag
    try:
        out = git.run(repo, "log", span, "--merges", "--format=%H%x1f%s", "--max-count=200")
    except git.ReviewGitError:
        return []
    rows = []
    for line in out.splitlines():
        sha, _, subject = line.partition("\x1f")
        match = re.search(r"#(\d+)", subject)
        rows.append(
            {"sha": sha[:7], "subject": subject[:120], "pr": int(match[1]) if match else None}
        )
    return rows


def _module_of(path: str) -> str:
    parts = PurePosixPath(path).parts
    if parts[:2] == ("web", "src"):
        return "web/src/" + (parts[2] if len(parts) > 3 else "")
    if parts[:2] == ("src", "duckterm"):
        return "src/duckterm/" + (parts[2] if len(parts) > 3 else "")
    if parts[:1] == ("mac",):
        return "mac"
    if parts[:1] == ("scripts",):
        return "scripts"
    return str(PurePosixPath(path).parent) or "."


def _python_imports(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {n for n in names if n.startswith("duckterm")}


def import_graph(repo: str, rev: str) -> dict[str, Any]:
    """Files grouped into coarse modules with the import edges between them."""
    try:
        listing = git.run(repo, "ls-tree", "-r", "--name-only", rev, timeout=60).splitlines()
    except git.ReviewGitError:
        listing = []
    files = [p for p in listing if symbols.indexable(p)][: symbols.MAX_FILES]
    contents = symbols.blobs(repo, rev, files)
    groups: dict[str, list[str]] = defaultdict(list)
    for path in files:
        groups[_module_of(path)].append(path)
    by_pymodule = {}
    for path in files:
        if path.endswith(".py") and path.startswith("src/"):
            dotted = path[len("src/") : -3].replace("/", ".").removesuffix(".__init__")
            by_pymodule[dotted] = path
    edges: Counter[tuple[str, str]] = Counter()
    for path, text in contents.items():
        source = _module_of(path)
        if path.endswith(".py"):
            for name in _python_imports(text):
                target = None
                while name and target is None:
                    target = by_pymodule.get(name)
                    name = name.rpartition(".")[0]
                if target:
                    dest = _module_of(target)
                    if dest != source:
                        edges[(source, dest)] += 1
        else:
            for spec in TS_IMPORT.findall(text):
                if spec.startswith("."):
                    resolved = str((PurePosixPath(path).parent / spec).as_posix())
                    resolved = re.sub(r"^(\./)+", "", resolved).replace("/./", "/")
                    dest = _module_of(resolved)
                    if dest != source:
                        edges[(source, dest)] += 1
    return {
        "rev": rev,
        "groups": {k: sorted(v) for k, v in sorted(groups.items())},
        "edges": [[a, b, n] for (a, b), n in edges.most_common(300)],
    }


def _prompt(tag: str, graph: dict[str, Any]) -> str:
    groups = "\n".join(
        f"- {name} ({len(paths)} files): {', '.join(PurePosixPath(p).name for p in paths[:12])}"
        for name, paths in graph["groups"].items()
    )
    edges = "\n".join(f"- {a} -> {b} ({n} imports)" for a, b, n in graph["edges"][:200])
    return (
        f"{cards.EVIDENCE}\n\n# Repository at {tag}\n\n## File groups\n{groups}\n\n"
        f"## Import edges between groups\n{edges or '- (none)'}\n\n"
        "# Task: Architecture map\n"
        f"Describe this release as at most {MAX_MODULES} modules a maintainer would name, each "
        "with an id (short, stable, lowercase), a name, a lane (Clients for the web dashboard "
        "and Mac app, Server for the HTTP server and persistence, Runtime for agent harness "
        "integration, hooks and worktrees), the file groups above that make it up (use the "
        "group names exactly), and one sentence on what it does. Then edges: [from_id, "
        "to_id] for the dependencies that matter, from the import edges above.\n\n"
        f"Reply with one JSON object matching this schema and nothing else:\n{json.dumps(SCHEMA)}"
    )


def map_dir(repo: str, slug: str | None) -> Path:
    return store.review_dir(repo, slug, MAP_FILE).parent / MAP_FILE


def load_map(repo: str, slug: str | None, tag: str) -> dict[str, Any] | None:
    value = store.load(map_dir(repo, slug), f"{store.ref_key(tag)}.json")
    return value or None


def map_status(repo: str, slug: str | None, tags: list[str]) -> dict[str, str]:
    directory = map_dir(repo, slug)
    out = {}
    for tag in tags:
        entry = store.load(directory, f"{store.ref_key(tag)}.json")
        out[tag] = str(entry.get("status") or "missing") if entry else "missing"
    return out


def _save(repo: str, slug: str | None, tag: str, value: dict[str, Any]) -> None:
    store.save(map_dir(repo, slug), f"{store.ref_key(tag)}.json", value)


async def build_map(repo: str, slug: str | None, tag: str) -> dict[str, Any]:
    """Generate and cache the module map for one tag."""
    existing = load_map(repo, slug, tag)
    if existing and existing.get("status") == "ready":
        return existing
    _save(repo, slug, tag, {"tag": tag, "status": "pending", "at": int(time.time())})
    graph = import_graph(repo, tag)
    if not backend.ready():
        value = {
            "tag": tag,
            "status": "omitted",
            "reason": "No Claude Code or Codex CLI is installed to describe the release",
            "graph": graph,
            "at": int(time.time()),
        }
        _save(repo, slug, tag, value)
        return value
    try:
        reply = (
            await backend.complete(
                _prompt(tag, graph), tier="fast", schema=SCHEMA, feature="release-map"
            )
        ).text
        parsed = cards._parse_reply(reply)
        if parsed is None or "modules" not in parsed:
            raise APIError(502, "The model did not return a module map")
    except APIError as exc:
        value = {
            "tag": tag,
            "status": "omitted",
            "reason": str(exc),
            "graph": graph,
            "at": int(time.time()),
        }
        _save(repo, slug, tag, value)
        return value
    known_groups = set(graph["groups"])
    modules = []
    for module in parsed.get("modules", [])[:MAX_MODULES]:
        if not isinstance(module, dict) or not module.get("id"):
            continue
        files = [f for f in module.get("files", []) if f in known_groups]
        modules.append(
            {
                "id": str(module["id"]),
                "name": str(module.get("name") or module["id"]),
                "lane": module.get("lane") if module.get("lane") in LANES else "Server",
                "files": files,
                "what": str(module.get("what") or ""),
            }
        )
    ids = {m["id"] for m in modules}
    edges = [
        [str(a), str(b)]
        for a, b in (e for e in parsed.get("edges", []) if isinstance(e, list) and len(e) == 2)
        if str(a) in ids and str(b) in ids
    ]
    value = {
        "tag": tag,
        "status": "ready",
        "modules": modules,
        "edges": edges,
        "graph": {"groups": {k: len(v) for k, v in graph["groups"].items()}},
        "at": int(time.time()),
    }
    _save(repo, slug, tag, value)
    return value


def compare(repo: str, older: dict[str, Any], newer: dict[str, Any]) -> dict[str, Any]:
    """Added, removed, changed and unchanged modules between two maps.
    Changed means a file in the module changed between the two tags."""
    try:
        changed_files = set(
            git.run(
                repo, "diff", "--name-only", older["tag"], newer["tag"], timeout=60
            ).splitlines()
        )
    except git.ReviewGitError:
        changed_files = set()
    changed_groups = {_module_of(p) for p in changed_files}
    old_ids = {m["id"]: m for m in older.get("modules", [])}
    new_ids = {m["id"]: m for m in newer.get("modules", [])}
    marks: dict[str, str] = {}
    for module_id, module in new_ids.items():
        if module_id not in old_ids:
            marks[module_id] = "added"
        elif any(group in changed_groups for group in module["files"]):
            marks[module_id] = "changed"
        else:
            marks[module_id] = "unchanged"
    for module_id in old_ids:
        if module_id not in new_ids:
            marks[module_id] = "removed"
    old_edges = {tuple(e) for e in older.get("edges", [])}
    new_edges = {tuple(e) for e in newer.get("edges", [])}
    return {
        "older": older["tag"],
        "newer": newer["tag"],
        "marks": marks,
        "removed_modules": [old_ids[i] for i in old_ids if i not in new_ids],
        "edges_added": [list(e) for e in sorted(new_edges - old_edges)],
        "edges_removed": [list(e) for e in sorted(old_edges - new_edges)],
        "files_changed": len(changed_files),
    }


def touched_releases(repo: str, rows: list[dict[str, Any]], paths: list[str]) -> list[str]:
    """Tags whose span changed any of the given paths (the PR's files)."""
    if not paths:
        return []
    out = []
    for row in rows:
        span = f"{row['previous']}..{row['tag']}" if row["previous"] else row["tag"]
        try:
            changed = git.run(
                repo,
                "diff",
                "--name-only",
                f"{row['previous']}" if row["previous"] else f"{row['tag']}~1",
                row["tag"],
                "--",
                *paths,
                timeout=30,
            )
        except git.ReviewGitError:
            continue
        if changed.strip():
            out.append(row["tag"])
        del span
    return out
