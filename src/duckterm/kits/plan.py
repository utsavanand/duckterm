"""A dry run of an install (§6 Review, `duckterm kit validate`): every file
and config edit per harness, the conflicts, and the result rows. Nothing is
written and nothing the kit ships runs."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from duckterm.kits import adapters, shim
from duckterm.kits import manifest as m
from duckterm.kits import validate as v

RECORD = ".duckterm/installed.json"


class PathEscape(ValueError):
    pass


def written_roots() -> frozenset[str]:
    """The only top-level names the adapters ever write under a project. A
    record path outside them (.git/, a source file, a gitignored local file
    the adapters never touch) is refused before anything reads it."""
    roots = {"AGENTS.md", "CLAUDE.md", ".duckterm", ".duckterm-rules.json"}
    for a in adapters.ADAPTERS.values():
        for value in (
            *a.skill_roots,
            a.agents_dir,
            a.hooks_file,
            a.hooks_dir,
            a.mcp_file,
            a.settings_file,
            a.knowledge_paths,
            a.rules_dir,
        ):
            if value:
                roots.add(Path(value).parts[0])
    return frozenset(roots)


def inside(project: Path, rel: str) -> Path:
    """`project / rel`, refused unless it is a path the adapters write, no
    part of it is a symlink, and it stays inside the project. The install
    record and component names come from the repository, so neither is
    trusted to name a path; and a repository can commit a link such as
    `.claude -> .git`, so an allowed name must not be followed into a refused
    one. Symlinks are refused outright rather than resolved, as validate does
    for kit sources."""
    parts = Path(rel).parts
    if not rel or Path(rel).is_absolute() or ".." in parts:
        raise PathEscape(f"{rel!r} must be a relative path inside the project")
    if ".git" in parts or parts[0] not in written_roots():
        raise PathEscape(f"{rel!r} is not a path skill kits write")
    target = project
    for part in parts:
        target = target / part
        if target.is_symlink():
            where = target.relative_to(project).as_posix()
            raise PathEscape(
                f"{rel!r} goes through a symlink at {where!r}; skill kits write real files"
            )
    if not target.resolve().is_relative_to(project.resolve()):
        raise PathEscape(f"{rel!r} resolves outside the project")
    return target


@dataclass
class Conflict:
    path: str
    kind: str  # file-exists | other-kit | conflicts-with | name-clash | escapes
    detail: str
    choices: tuple[str, ...]
    component: str = ""


@dataclass
class Plan:
    kit: m.Kit
    project: Path
    harnesses: list[str]
    options: dict[str, str]
    placements: list[adapters.Placement]
    conflicts: list[Conflict]
    findings: list[v.Finding]
    record: dict[str, Any]

    @property
    def errors(self) -> list[v.Finding]:
        return [f for f in self.findings if f.level == "error"]

    def rows(self) -> list[dict[str, Any]]:
        """One row per component, one status per chosen harness (§9)."""
        rows: dict[str, dict[str, Any]] = {}
        for p in self.placements:
            row = rows.setdefault(
                p.component,
                {"component": p.component, "kind": p.kind, "harnesses": {}, "notes": []},
            )
            targets = self.harnesses if p.harness == adapters.SHARED else [p.harness]
            for h in targets:
                current = row["harnesses"].get(h)
                if current is None or _worse(p.status, current):
                    row["harnesses"][h] = p.status
            if p.note and p.note not in row["notes"]:
                row["notes"].append(
                    ("" if p.harness == adapters.SHARED else f"{p.harness}: ") + p.note
                )
        return list(rows.values())

    def files(self) -> list[adapters.Placement]:
        return [p for p in self.placements if p.writes()]

    def to_json(self) -> dict[str, Any]:
        return {
            "kit": {"name": self.kit.name, "version": self.kit.version},
            "project": str(self.project),
            "harnesses": self.harnesses,
            "options": self.options,
            "findings": [
                {"level": f.level, "component": f.component, "message": f.message}
                for f in self.findings
            ],
            "conflicts": [c.__dict__ for c in self.conflicts],
            "rows": self.rows(),
            "files": [
                {"path": p.path, "op": p.op, "harness": p.harness, "component": p.component}
                for p in self.files()
            ],
        }


_RANK = {
    adapters.INSTALLED: 0,
    adapters.CHECKED: 0,
    adapters.OFFERED: 0,
    adapters.NOT_APPLICABLE: 1,
    adapters.NEEDS_TRUST: 2,
    adapters.NOT_VERIFIED: 3,
    adapters.MISSING: 4,
    adapters.NOT_SUPPORTED: 5,
}


def _worse(a: str, b: str) -> bool:
    return _RANK.get(a, 3) > _RANK.get(b, 3)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_record(project: Path) -> dict[str, Any]:
    path = project / RECORD
    if not path.is_file():
        return {"kits": {}}
    data = json.loads(path.read_text())
    return data if isinstance(data, dict) and isinstance(data.get("kits"), dict) else {"kits": {}}


def detect_harnesses() -> list[str]:
    return [a.id for a in adapters.ADAPTERS.values() if a.installed_here()]


def build(
    kit: m.Kit, project: Path, harnesses: list[str], options: dict[str, str] | None = None
) -> Plan:
    project = Path(project)
    unknown = [h for h in harnesses if h not in adapters.ADAPTERS]
    if unknown:
        raise ValueError(
            f"unknown harness {unknown[0]!r}; choose from {', '.join(adapters.ADAPTERS)}"
        )
    chosen = list(dict.fromkeys(harnesses))
    opts = {**kit.default_options(), **(options or {})}
    findings = v.validate(kit)
    for name, value in opts.items():
        spec = kit.options.get(name)
        if spec is None or value not in (spec.get("choices") or []):
            findings.append(
                v.Finding("error", "manifest", f"option {name}={value!r} is not a choice")
            )
    record = load_record(project)
    placements: list[adapters.Placement] = []
    conflicts: list[Conflict] = []
    if kit.legacy:
        findings.append(
            v.Finding(
                "error", "manifest", "a legacy manifest lists no components; nothing to install"
            )
        )
        return Plan(kit, project, chosen, opts, placements, conflicts, findings, record)

    shim_source = Path(shim.__file__).read_bytes()
    roots = adapters.skill_roots_for(chosen)
    for c in kit.components:
        active = [h for h in chosen if c.applies(h, opts)]
        if not active and c.kind != "flow":
            placements.append(
                adapters.Placement(
                    kit.component_id(c),
                    adapters.SHARED,
                    c.kind,
                    "none",
                    status=adapters.NOT_APPLICABLE,
                    note=_why_inactive(c, chosen, opts),
                )
            )
            continue
        if c.kind == "knowledge":
            placements += adapters.place_knowledge(kit, c, active, roots)
        elif c.kind == "skill":
            placements += adapters.place_skill(kit, c, active, roots)
        elif c.kind == "agent":
            placements += adapters.place_agent(kit, c, active)
        elif c.kind == "hook":
            placements += adapters.place_hook(kit, c, active, shim_source)
        elif c.kind == "permission":
            placements += adapters.place_permission(kit, c, active)
        elif c.kind == "tool":
            placements += adapters.place_tool(kit, c, active)
        elif c.kind == "settings":
            placements += adapters.place_settings(kit, c, active)
        elif c.kind == "flow":
            placements += adapters.place_flow(kit, c, active)
    for team in kit.teams:
        placements.append(adapters.place_team(kit, team))
    placements = _dedupe(placements)
    conflicts += _conflicts(kit, project, placements, record)
    return Plan(kit, project, chosen, opts, placements, conflicts, findings, record)


def _why_inactive(c: m.Component, chosen: list[str], opts: dict[str, str]) -> str:
    if c.only_harness and not set(c.only_harness) & set(chosen):
        return f"only for {', '.join(c.only_harness)}"
    return "option " + ", ".join(f"{k}={'|'.join(v)}" for k, v in c.when.items()) + " not chosen"


def _dedupe(placements: list[adapters.Placement]) -> list[adapters.Placement]:
    """One write per path: the shim, a kit's skills.json and exec scripts are
    placed once per component that needs them and kept once."""
    seen: set[tuple[str, str]] = set()
    out = []
    for p in placements:
        key = (p.op, p.path) if p.op in ("write", "copy-tree", "claude-import") and p.path else None
        if key:
            if key in seen:
                continue
            seen.add(key)
        out.append(p)
    return out


def _conflicts(
    kit: m.Kit, project: Path, placements: list[adapters.Placement], record: dict[str, Any]
) -> list[Conflict]:
    out: list[Conflict] = []
    installed = record.get("kits", {})
    for other in kit.conflicts_with:
        if other in installed:
            out.append(
                Conflict(
                    "",
                    "conflicts-with",
                    f"{kit.name} must not be installed with {other}, which is installed",
                    ("refuse",),
                )
            )
    owned: dict[str, tuple[str, str]] = {}  # path → (kit, sha)
    names: dict[tuple[str, str], str] = {}  # (kind, name) → kit
    for owner_kit, entry in installed.items():
        for e in entry.get("entries", []):
            if isinstance(e, dict) and e.get("sha256") and isinstance(e.get("path"), str):
                owned[e["path"]] = (owner_kit, e["sha256"])
        for cid in entry.get("components", []):
            parts = str(cid).split("/")
            if len(parts) == 3:
                names[(parts[1], parts[2])] = owner_kit
    for p in placements:
        if not p.writes() or not p.path:
            continue
        try:
            inside(project, p.path)
        except PathEscape as exc:
            if not any(c.kind == "escapes" and c.path == p.path for c in out):
                out.append(Conflict(p.path, "escapes", str(exc), ("refuse",), p.component))
            continue
        if p.op in ("write", "copy-tree"):
            for rel, data in _materialise(p):
                try:
                    target = inside(project, rel)
                except PathEscape as exc:
                    out.append(Conflict(rel, "escapes", str(exc), ("refuse",), p.component))
                    continue
                if not target.exists():
                    continue
                current = sha256(target.read_bytes()) if target.is_file() else ""
                owner = owned.get(rel)
                if owner is None:
                    if current == sha256(data):
                        continue  # identical hand-placed file: adopting it changes nothing
                    out.append(
                        Conflict(
                            rel,
                            "file-exists",
                            "exists and is not from a kit",
                            ("keep", "replace"),
                            p.component,
                        )
                    )
                elif owner[0] != kit.name:
                    out.append(
                        Conflict(
                            rel,
                            "other-kit",
                            f"written by kit {owner[0]}",
                            ("keep theirs", "use this one"),
                            p.component,
                        )
                    )
                elif owner[1] != current:
                    out.append(
                        Conflict(
                            rel,
                            "file-exists",
                            "edited since this kit wrote it",
                            ("keep", "replace"),
                            p.component,
                        )
                    )
        if p.kind in ("skill", "agent", "tool", "flow") and p.op != "none":
            _, kind, cname = p.component.split("/", 2)
            holder = names.get((kind, cname))
            if holder and holder != kit.name:
                c = Conflict(
                    "",
                    "name-clash",
                    f"{kind} {cname!r} is also in kit {holder}",
                    ("keep theirs", "use this one", f"rename to {kit.name}:{cname}"),
                    p.component,
                )
                if c not in out:
                    out.append(c)
    return out


def _materialise(p: adapters.Placement) -> list[tuple[str, bytes]]:
    """(project-relative path, bytes) for every file a placement writes.
    Symlinks are never followed: validate refuses them, and a kit that slipped
    one past it still cannot read outside its own tree here."""
    if p.op == "write":
        return [(p.path, p.content or b"")]
    if p.op == "copy-tree" and p.source is not None and not p.source.is_symlink():
        if p.source.is_file():
            return [(p.path, p.source.read_bytes())]
        out = []
        for folder, dirs, files in os.walk(p.source, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not Path(folder, d).is_symlink())
            for name in sorted(files):
                f = Path(folder, name)
                if f.is_file() and not f.is_symlink():
                    out.append((f"{p.path}/{f.relative_to(p.source).as_posix()}", f.read_bytes()))
        return out
    return []
