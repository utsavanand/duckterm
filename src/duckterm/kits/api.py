"""What the Skill kits page asks the server for, as plain functions over the
kit engine: the installed kits of a project with their procedures and flows,
a dry-run plan for a kit folder, install, uninstall, verify, and flow runs.
The server's handlers parse the request and call these; the CLI uses the same
engine underneath, so the page and the terminal cannot disagree."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import tomllib
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

from duckterm.kits import adapters, install, runner, validate
from duckterm.kits import manifest as m
from duckterm.kits import plan as planning


class KitApiError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _project(value: Any) -> Path:
    text = str(value or "").strip()
    if not text:
        raise KitApiError(400, "project is required")
    path = Path(text).expanduser()
    if not path.is_dir():
        raise KitApiError(400, f"no such directory: {path}")
    return path.resolve()


def _kit(value: Any) -> m.Kit:
    text = str(value or "").strip()
    if not text:
        raise KitApiError(400, "kit is required (a folder holding skill-kit.toml)")
    path = Path(text).expanduser()
    if not path.is_dir() and not (path.is_file() and path.suffix == ".toml"):
        raise KitApiError(400, f"no such kit folder or manifest: {path}")
    try:
        return m.load(path)
    except (m.ManifestError, OSError, ValueError) as exc:
        raise KitApiError(400, str(exc)) from exc


def _harnesses(value: Any) -> list[str]:
    if not value:
        detected = planning.detect_harnesses()
        if not detected:
            raise KitApiError(400, "no harness on PATH; choose one")
        return detected
    if not isinstance(value, list) or not all(isinstance(h, str) for h in value):
        raise KitApiError(400, "harnesses must be a list of names")
    unknown = [h for h in value if h not in adapters.ADAPTERS]
    if unknown:
        raise KitApiError(400, f"unknown harness {unknown[0]!r}")
    return list(dict.fromkeys(value))


def _fallbacks(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise KitApiError(400, "fallbacks must be a list of component ids")
    return list(dict.fromkeys(value))


def _options(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise KitApiError(400, "options must be an object of name → choice")
    return {str(k): str(v) for k, v in value.items()}


def harnesses_present() -> dict[str, bool]:
    return {a.id: a.installed_here() for a in adapters.ADAPTERS.values()}


def procedures(kit: m.Kit) -> list[dict[str, Any]]:
    """The team templates a kit ships, summarised for a page: roles with their
    purpose and skills, and the rules between them."""
    out = []
    for rel in kit.teams:
        path = kit.root / str(rel)
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        roles = data.get("roles")
        if not isinstance(roles, dict):
            roles = {}
        out.append(
            {
                "name": str(data.get("name") or Path(str(rel)).stem),
                "description": str(data.get("description") or ""),
                "template": str(rel),
                "roles": {
                    str(name): {
                        "purpose": (
                            str((spec.get("card") or {}).get("purpose", ""))
                            if isinstance(spec, dict)
                            else ""
                        ),
                        "skills": (
                            [str(s) for s in (spec.get("skills") or [])]
                            if isinstance(spec, dict)
                            else []
                        ),
                    }
                    for name, spec in roles.items()
                },
                "rules": [r for r in data.get("rules", []) if isinstance(r, dict)],
            }
        )
    return out


def _first_line(text: str) -> str:
    for line in text.splitlines():
        t = line.strip()
        if t and not t.startswith(("#", "---", "**Severity", "**Category")):
            return t[:220]
    return ""


def _script_header(path: Path) -> str:
    try:
        lines = path.read_text().splitlines()[1:6]
    except OSError:
        return ""
    return " ".join(line[2:].strip() for line in lines if line.startswith("# "))[:220]


def details(kit: m.Kit) -> dict[str, list[dict[str, Any]]]:
    """What each component is, for the kit page: one entry per component with
    the fields a person opens it for. Stored in the install record, so the page
    needs no kit folder afterwards."""
    out: dict[str, list[dict[str, Any]]] = {}
    for c in kit.components:
        e: dict[str, Any] = {"name": c.name}
        f = c.fields
        if c.kind == "skill" and c.path:
            try:
                front = m.frontmatter((kit.root / c.path / "SKILL.md").read_text())
            except OSError:
                front = {}
            e.update(
                description=" ".join(str(front.get("description", "")).split()),
                effects=f.get("effects", "workspace"),
                invocation=f.get("invocation", "auto"),
                outputs=(
                    sorted(str(k) for k in f["outputs"])
                    if isinstance(f.get("outputs"), dict)
                    else []
                ),
            )
        elif c.kind == "agent" and c.path:
            try:
                front = m.frontmatter((kit.root / c.path).read_text())
            except OSError:
                front = {}
            tools = [
                t.strip("- ").strip()
                for t in str(front.get("tools", "")).splitlines()
                if t.strip("- ").strip()
            ]
            e.update(
                description=" ".join(str(front.get("description", "")).split()),
                model=str(front.get("model", "")),
                tools=tools[:12],
            )
        elif c.kind == "knowledge" and c.path:
            load = f.get("load", "always")
            try:
                text = (kit.root / c.path).read_text()
            except OSError:
                text = ""
            e.update(
                load=(
                    "always"
                    if load == "always"
                    else (
                        "on demand"
                        if load == "on-demand"
                        else (
                            f"for {', '.join(str(x) for x in load.get('paths', []))}"
                            if isinstance(load, dict)
                            else str(load)
                        )
                    )
                ),
                description=str(f.get("description") or _first_line(text)),
                lines=len(text.splitlines()),
            )
        elif c.kind == "hook":
            e.update(
                event=str(f.get("event", "")),
                match=str(f.get("match", "")),
                blocking=bool(f.get("blocking")),
                timeout=f.get("timeout", 5),
                description=_script_header(kit.root / str(f.get("command", ""))),
            )
        elif c.kind == "permission":
            rules = [r for r in f.get("rules", []) if isinstance(r, dict)]
            e.update(
                rules=[
                    {
                        "tool": str(r.get("tool")),
                        "match": str(r.get("match")),
                        "decision": str(r.get("decision")),
                    }
                    for r in rules
                ]
            )
        elif c.kind == "flow" and c.path:
            try:
                flow = json.loads((kit.root / c.path).read_text())
                steps = flow.get("steps", []) if isinstance(flow, dict) else []
            except (OSError, ValueError):
                flow, steps = {}, []
            e.update(
                description=str(flow.get("description", "")) if isinstance(flow, dict) else "",
                inputs=(
                    sorted(str(k) for k in (flow.get("inputs") or {}))
                    if isinstance(flow, dict)
                    else []
                ),
                steps=[
                    {
                        "id": str(st.get("id")),
                        "kind": runner.step_kind(st) if isinstance(st, dict) else "?",
                    }
                    for st in steps
                    if isinstance(st, dict) and st.get("id")
                ],
                trigger=None,
            )
        elif c.kind == "settings":
            try:
                keys = (
                    sorted(json.loads((kit.root / str(c.path)).read_text()).keys())
                    if c.path
                    else []
                )
            except (OSError, ValueError, AttributeError):
                keys = []
            e.update(harness=str(f.get("harness", "")), keys=keys)
        elif c.kind == "tool":
            spec = f.get("mcp") or f.get("cli") or {}
            e.update(
                form="mcp" if f.get("mcp") else "cli",
                description=str(spec.get("doctor", "")) if isinstance(spec, dict) else "",
            )
        out.setdefault(c.kind, []).append(e)
    return out


def inventory(kit: m.Kit) -> dict[str, Any]:
    """What a kit folder holds, before any plan: counts and names per kind,
    its options and procedures, and validate's findings."""
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for c in kit.components:
        entry: dict[str, Any] = {"name": c.name}
        for key in ("effects", "event", "match", "load", "invocation", "harness"):
            if key in c.fields:
                entry[key] = c.fields[key]
        if c.when:
            entry["when"] = c.when
        if c.kind == "skill" and isinstance(c.fields.get("outputs"), dict):
            entry["outputs"] = sorted(str(k) for k in c.fields["outputs"])
        by_kind.setdefault(c.kind, []).append(entry)
    findings = validate.validate(kit)
    return {
        "name": kit.name,
        "version": kit.version,
        "description": kit.description,
        "root": str(kit.root),
        "author": kit.raw.get("author"),
        "license": kit.raw.get("license"),
        "options": kit.options,
        "components": by_kind,
        "component_count": len(kit.components),
        "procedures": procedures(kit),
        "findings": [
            {"level": f.level, "component": f.component, "message": f.message} for f in findings
        ],
        "valid": not any(f.level == "error" for f in findings),
    }


def overview(project_value: Any) -> dict[str, Any]:
    """The Skill kits page: every kit the project's record lists, with its
    verify state, flows, procedures and runs."""
    project = _project(project_value)
    record = planning.load_record(project)
    reports = {r.kit: r for r in install.verify(project)}
    kits = []
    for name, entry in record["kits"].items():
        report = reports.get(name)
        components = [str(c) for c in entry.get("components", [])]
        skills_file = project / runner.FLOWS / name / runner.SKILLS_FILE
        skills: dict[str, Any] = {}
        if skills_file.is_file():
            try:
                skills = json.loads(skills_file.read_text()).get("skills") or {}
            except (OSError, ValueError):
                skills = {}
        kits.append(
            {
                "name": name,
                "version": entry.get("version"),
                "harnesses": entry.get("harnesses", []),
                "options": entry.get("options", {}),
                "installed_at": entry.get("installed_at"),
                "entries": len(entry.get("entries", [])),
                "trusted": install._trusted(project, name, entry),
                "verify": {
                    "ok": report.ok() if report else None,
                    "missing": report.missing if report else [],
                    "drifted": report.drifted if report else [],
                },
                "flows": [c.split("/", 2)[2] for c in components if c.split("/")[1:2] == ["flow"]],
                "skills": {
                    str(n): {
                        "description": str(v.get("description", "")),
                        "effects": v.get("effects"),
                    }
                    for n, v in skills.items()
                    if isinstance(v, dict)
                },
                "components": components,
                "procedures": entry.get("procedures", []),
                "details": entry.get("details", {}),
            }
        )
    return {
        "project": str(project),
        "kits": kits,
        "procedures": [
            {**p, "kit": k["name"], "kit_version": k["version"]}
            for k in kits
            for p in k["procedures"]
        ],
        "harnesses_present": harnesses_present(),
        "runs": runner.list_runs(project),
    }


def register(body: dict[str, Any]) -> dict[str, Any]:
    """Add a kit to DuckTerm: read its manifest (folder or skill-kit.toml),
    nothing else. The server stores name and path; installing into a
    workspace is a later, separate step."""
    kit = _kit(body.get("kit"))
    text = str(body.get("kit") or "").strip()
    path = Path(text).expanduser().resolve()
    if kit.legacy:
        raise KitApiError(
            400,
            f"{path} holds {m.LEGACY_MANIFEST}, the retired format, which lists no "
            f"components; write a {m.MANIFEST} for it first",
        )
    return {
        "name": kit.name,
        "path": str(path),
        "version": kit.version,
        "description": kit.description,
    }


def repositories(paths: list[str]) -> list[str]:
    """The repositories a workspace's sessions run in directly, from the
    folders its session rows name: a folder inside a main working tree
    counts as that repository; a linked worktree counts for nothing here,
    because its main checkout is where other workspaces' sessions may run
    (hooks included), and the worktree itself gets the kit at its next
    session start (policy_target). A plain folder, a deleted one or a bare
    repository is left out. Git is asked every time: metadata changes while
    DuckTerm runs (a `git init`, a removed `.git`), and a Git directory
    need not be named `.git` (`--separate-git-dir`)."""
    import subprocess

    out: set[str] = set()
    for folder in sorted({str(p) for p in paths}):
        if not os.path.isdir(folder):
            continue
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "-C",
                    folder,
                    "rev-parse",
                    "--path-format=absolute",
                    "--absolute-git-dir",
                    "--git-common-dir",
                    "--show-toplevel",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        parts = proc.stdout.strip().splitlines() if proc.returncode == 0 else []
        if len(parts) != 3:
            continue
        git_dir, common, top = parts
        if git_dir != common:  # a linked worktree: its next session start installs there
            continue
        if os.path.isdir(top):
            out.add(top)
    return sorted(out)


def catalog(entries: list[dict[str, Any]], workspaces: list[dict[str, Any]]) -> dict[str, Any]:
    """The Skill kits page: every kit added to DuckTerm, what it holds, and
    the workspaces it is installed in. `entries` are the registry rows
    (name, path); `workspaces` are {name, repositories: [path]} where the
    repositories are where the workspace's sessions run."""
    projects = sorted({str(r) for w in workspaces for r in w.get("repositories", [])})
    records: dict[str, dict[str, Any]] = {}
    reports: dict[str, dict[str, Any]] = {}
    runs: dict[str, list[dict[str, Any]]] = {}
    for project in projects:
        path = Path(project)
        if not path.is_dir():
            continue
        try:
            records[project] = planning.load_record(path)
        except (OSError, ValueError):
            continue
        if records[project]["kits"]:
            reports[project] = {r.kit: r for r in install.verify(path)}
            runs[project] = runner.list_runs(path)
    kits = []
    for row in entries:
        entry: dict[str, Any] = {
            "name": str(row["name"]),
            "path": str(row["path"]),
            "installed_in": [],
        }
        try:
            kit = _kit(row["path"])
        except KitApiError as exc:
            entry["error"] = str(exc)
            kits.append(entry)
            continue
        entry["name"] = kit.name
        entry["version"] = kit.version
        entry["description"] = kit.description
        entry["inventory"] = inventory(kit)
        entry["details"] = details(kit)
        entry["procedures"] = procedures(kit)
        entry["flows"] = [Path(str(c.path)).stem for c in kit.by_kind("flow")]
        entry["runs"] = []
        entry["policy"] = policies().get(kit.name)
        entry["changed"] = bool(entry["policy"]) and entry["policy"].get("digest") != kit_digest(
            kit.root
        )
        entry["pending"] = [
            str(w.get("name", ""))
            for w in workspaces
            if policy_matches(entry["policy"], str(w.get("name", ""))) and not w.get("repositories")
        ]
        for w in workspaces:
            for project in w.get("repositories", []):
                installed = records.get(str(project), {"kits": {}})["kits"].get(kit.name)
                if not installed:
                    continue
                report = reports.get(str(project), {}).get(kit.name)
                entry["installed_in"].append(
                    {
                        "workspace": w.get("name", ""),
                        "project": str(project),
                        "version": installed.get("version"),
                        "harnesses": installed.get("harnesses", []),
                        "installed_at": installed.get("installed_at"),
                        "trusted": install._trusted(Path(str(project)), kit.name, installed),
                        "verify": {
                            "ok": report.ok() if report else None,
                            "missing": report.missing if report else [],
                            "drifted": report.drifted if report else [],
                        },
                    }
                )
                entry["runs"] += [
                    r
                    for r in runs.get(str(project), [])
                    if str(r.get("flow", "")).startswith(kit.name + "/")
                ]
        kits.append(entry)
    return {
        "kits": kits,
        "workspaces": workspaces,
        "harnesses": list(adapters.ADAPTERS),
        "harnesses_present": harnesses_present(),
    }


def plan_for(body: dict[str, Any]) -> dict[str, Any]:
    """The dry run. Without a project it is the kit alone against every
    harness (an empty folder): what each harness gets does not depend on the
    folder, only conflicts do."""
    kit = _kit(body.get("kit"))
    harnesses = _harnesses(body.get("harnesses"))
    options, fallbacks = _options(body.get("options")), _fallbacks(body.get("fallbacks"))
    try:
        if body.get("project"):
            p = planning.build(kit, _project(body.get("project")), harnesses, options, fallbacks)
        else:
            with tempfile.TemporaryDirectory(prefix="kit-plan-") as empty:
                p = planning.build(kit, Path(empty), harnesses, options, fallbacks)
    except ValueError as exc:
        raise KitApiError(400, str(exc)) from exc
    return {"inventory": inventory(kit), "plan": p.to_json()}


def review(
    body: dict[str, Any], registry: list[dict[str, Any]], workspaces: list[dict[str, Any]]
) -> dict[str, Any]:
    """What Enable would do for the chosen workspaces: the kit's matrix (one,
    for every harness), and per repository known now only what differs there:
    its conflicts and whether Codex trusts it (hooks run only then). `fixes`
    lists what the owner can put right from the review."""
    kit_name = str(body.get("kit") or "")
    scope = str(body.get("scope") or "")
    if scope not in ("all", "workspaces"):
        raise KitApiError(400, "scope must be 'all' or 'workspaces'")
    chosen = body.get("workspaces") or []
    if not isinstance(chosen, list) or not all(isinstance(w, str) for w in chosen):
        raise KitApiError(400, "workspaces must be a list of names")
    kit_path = _registry_path(registry, kit_name)
    kit = _kit(kit_path)
    options, fallbacks = _options(body.get("options")), _fallbacks(body.get("fallbacks"))
    policy = {"scope": scope, "workspaces": chosen}
    harnesses = list(adapters.ADAPTERS)
    matrix = plan_for(
        {"kit": kit_path, "harnesses": harnesses, "options": options, "fallbacks": fallbacks}
    )
    has_hooks = any(c.kind == "hook" for c in kit.components)
    repos: list[dict[str, Any]] = []
    for w in workspaces:
        name = str(w.get("name", ""))
        if not policy_matches(policy, name):
            continue
        for project in w.get("repositories", []):
            path = Path(str(project))
            if not path.is_dir():
                continue
            try:
                p = planning.build(kit, path, harnesses, options, fallbacks)
            except ValueError as exc:
                raise KitApiError(400, f"{project}: {exc}") from exc
            installed = kit_name in (p.record.get("kits") or {})
            repos.append(
                {
                    "workspace": name,
                    "project": str(project),
                    "installed": installed,
                    "conflicts": [c.__dict__ for c in p.conflicts],
                    "codex_trusted": codex_trusts(path) if has_hooks else None,
                }
            )
    fixes: list[dict[str, Any]] = []
    untrusted = [r["project"] for r in repos if r.get("codex_trusted") is False]
    if untrusted:
        fixes.append({"kind": "codex-trust", "projects": untrusted})
    for row in matrix["plan"]["rows"]:
        if row["kind"] != "knowledge":
            continue
        lacking = [
            h
            for h, status in row["harnesses"].items()
            if status == adapters.NOT_SUPPORTED
            and any(n.startswith(f"{h}: ") and "no path-scoped" in n for n in row["notes"])
        ]
        if lacking:
            fixes.append({"kind": "fallback", "component": row["component"], "harnesses": lacking})
    for r in repos:
        for c in r["conflicts"]:
            if c.get("kind") == "file-exists":
                fixes.append({"kind": "replace", "project": r["project"], "path": c["path"]})
    pending = [
        str(w.get("name", ""))
        for w in workspaces
        if policy_matches(policy, str(w.get("name", ""))) and not w.get("repositories")
    ]
    return {**matrix, "repositories": repos, "pending": pending, "fixes": fixes}


def codex_trusts(project: Path) -> bool:
    """Codex runs a project's hooks only once the owner trusted it; the record
    is `[projects."<path>"] trust_level = "trusted"` in ~/.codex/config.toml."""
    config = Path.home() / ".codex" / "config.toml"
    try:
        data = tomllib.loads(config.read_text())
    except (OSError, ValueError):
        return False
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return False
    for key in (str(project), str(Path(project).resolve())):
        entry = projects.get(key)
        if isinstance(entry, dict) and entry.get("trust_level") == "trusted":
            return True
    return False


def fix(body: dict[str, Any]) -> dict[str, Any]:
    """A Fix pressed in the review. `codex-trust`: mark each repository
    trusted in Codex's own config, the record Codex itself writes when the
    owner accepts its prompt; one click covers every kit and session there.
    Owner decision 2026-11-25: DuckTerm writes Codex trust only here, on the
    owner's click, never at install or SessionStart."""
    kind = str(body.get("kind") or "")
    if kind != "codex-trust":
        raise KitApiError(400, f"unknown fix {kind!r}")
    projects = body.get("projects") or []
    if not isinstance(projects, list) or not projects:
        raise KitApiError(400, "projects is required")
    done = []
    for raw in projects:
        project = _project(raw)
        if not (project / ".git").exists():
            raise KitApiError(400, f"{project} is not a git repository")
        _codex_trust(project)
        done.append(str(project))
    return {"kind": kind, "trusted": done}


def _codex_trust(project: Path) -> None:
    config = Path.home() / ".codex" / "config.toml"
    if config.is_symlink():
        raise KitApiError(500, f"{config} is a symlink; nothing is written through it")
    text = config.read_text() if config.is_file() else ""
    try:
        data = tomllib.loads(text)
    except ValueError as exc:
        raise KitApiError(409, f"{config} does not parse; fix it first: {exc}") from exc
    key = str(project)
    entry = (
        (data.get("projects") or {}).get(key) if isinstance(data.get("projects"), dict) else None
    )
    if isinstance(entry, dict) and entry.get("trust_level") == "trusted":
        return
    header = f"[projects.{json.dumps(key)}]"
    lines = text.splitlines()
    if isinstance(entry, dict):
        # the table exists with another level: replace its trust_level line
        start = next(i for i, line in enumerate(lines) if line.strip() == header)
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
            len(lines),
        )
        body_lines = [
            line for line in lines[start + 1 : end] if not line.strip().startswith("trust_level")
        ]
        lines[start:end] = [header, 'trust_level = "trusted"', *body_lines]
        out = "\n".join(lines) + "\n"
    else:
        out = text + ("" if not text or text.endswith("\n") else "\n")
        out += ("\n" if text.strip() else "") + header + "\n" + 'trust_level = "trusted"\n'
    tomllib.loads(out)  # never leave Codex a config it cannot read
    config.parent.mkdir(parents=True, exist_ok=True)
    _write_private(config, out)


def copilot_flags(project: str | Path, registry: list[dict[str, Any]]) -> list[str]:
    """The permission flags for a Copilot session in `project`: every kit the
    repository's record holds, read from the kit as added to DuckTerm."""
    try:
        record = planning.load_record(Path(project))
    except (OSError, ValueError):
        return []
    flags: list[str] = []
    for kit_name in record.get("kits") or {}:
        try:
            kit = _kit(_registry_path(registry, kit_name))
        except KitApiError:
            continue
        flags += adapters.copilot_flags(kit)
    return flags


def install_kit(body: dict[str, Any]) -> dict[str, Any]:
    kit = _kit(body.get("kit"))
    project = _project(body.get("project"))
    harnesses = _harnesses(body.get("harnesses"))
    try:
        p = planning.build(
            kit,
            project,
            harnesses,
            _options(body.get("options")),
            _fallbacks(body.get("fallbacks")),
        )
        report = install.apply(p, replace=bool(body.get("replace")))
    except (install.InstallRefused, ValueError, OSError) as exc:
        raise KitApiError(409, str(exc)) from exc
    return {"plan": p.to_json(), "report": report.__dict__}


def uninstall_kit(body: dict[str, Any]) -> dict[str, Any]:
    project = _project(body.get("project"))
    name = str(body.get("name") or "")
    try:
        report = install.uninstall(project, name)
    except (install.InstallRefused, ValueError, OSError) as exc:
        raise KitApiError(409, str(exc)) from exc
    return {"report": report.__dict__}


def verify_kits(body: dict[str, Any]) -> dict[str, Any]:
    project = _project(body.get("project"))
    return {"reports": [r.__dict__ for r in install.verify(project)]}


# ── teams: a workspace created from a kit's team template ────────────────────


def _teams_dir() -> Path:
    from duckterm.helpers import instance

    return instance.home() / "teams"


def _team_file(folder: str) -> Path:
    import urllib.parse

    return _teams_dir() / (urllib.parse.quote(folder, safe="") + ".json")


def team_for_folder(folder: str) -> dict[str, Any] | None:
    path = _team_file(folder)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def teams() -> dict[str, Any]:
    out = []
    folder = _teams_dir()
    if folder.is_dir():
        for f in sorted(folder.glob("*.json")):
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                out.append(data)
    return {"teams": out}


def create_team(body: dict[str, Any]) -> dict[str, Any]:
    """The record a team is: the workspace's name, the procedure (roles and
    rules) copied from the kit's template at this moment, and where it came
    from. The server creates the workspace itself; this writes the record
    under the DuckTerm home, beside the kit records, never into the
    repository."""
    kit_name = str(body.get("kit") or "")
    team_name = str(body.get("team") or "")
    folder = str(body.get("name") or team_name).strip()
    if not kit_name or not team_name:
        raise KitApiError(400, "kit and team are required")
    if not folder or "/" in folder or folder.startswith("."):
        raise KitApiError(400, "a workspace name is required, without slashes")
    project: Path | None = None
    if body.get("project"):
        # The kit is installed somewhere: copy the procedure as installed.
        project = _project(body.get("project"))
        record = planning.load_record(project)
        entry = record["kits"].get(kit_name)
        if entry is None:
            raise KitApiError(404, f"kit {kit_name!r} is not installed in {project}")
        version = entry.get("version")
        offered = entry.get("procedures", [])
    elif body.get("kit_path"):
        # Not installed yet: the procedure comes from the manifest, and the
        # kit is enabled for the new workspace so its first session gets it.
        kit = _kit(body.get("kit_path"))
        if kit.name != kit_name:
            raise KitApiError(400, f"{body.get('kit_path')} holds {kit.name!r}, not {kit_name!r}")
        version = kit.version
        offered = procedures(kit)
    else:
        raise KitApiError(400, "project (an install) or kit_path (an added kit) is required")
    template = next((p for p in offered if p.get("name") == team_name), None)
    if template is None:
        raise KitApiError(404, f"kit {kit_name!r} offers no team {team_name!r}")
    existing = team_for_folder(folder)
    if existing is not None:
        raise KitApiError(409, f"workspace {folder!r} is already a team ({existing.get('team')})")
    import time

    data = {
        "folder": folder,
        "team": team_name,
        "description": template.get("description", ""),
        "kit": kit_name,
        "kit_version": version,
        "project": str(project) if project else None,
        "roles": template.get("roles", {}),
        "rules": template.get("rules", []),
        "created_at": int(time.time()),
    }
    # An exclusive create: two requests for the same name racing past the
    # check above cannot both write, so a failed creation's recovery removes
    # only its own record.
    try:
        _create_private(_team_file(folder), json.dumps(data, indent=2) + "\n")
    except FileExistsError:
        raise KitApiError(409, f"workspace {folder!r} is already a team") from None
    return {"team": data}


def remove_team(folder: str) -> None:
    """Take a team record back out, for a creation that could not finish (the
    workspace itself failed to register) so the next attempt is not a 409."""
    _team_file(folder).unlink(missing_ok=True)


# ── enable: which workspaces a kit is for, and installing accordingly ───────


def kit_digest(root: Path) -> str:
    """One hash over every regular file under the kit folder: path, size and
    content, `.git` left out, symlinks left out (validate refuses them). Enable
    pins the digest of the tree the owner reviewed; a folder that no longer
    hashes the same (a `git pull`, an edit under ~/Downloads) installs nowhere
    until the owner enables it again."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        h.update(f"{path.relative_to(root).as_posix()}\0{len(data)}\0".encode())
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def _policy_file() -> Path:
    return _teams_dir().parent / "kit-policy.json"


def policies() -> dict[str, dict[str, Any]]:
    """Per kit: {"scope": "all"} or {"scope": "workspaces", "workspaces": [names]}.
    A kit with no entry is added but enabled nowhere."""
    try:
        data = json.loads(_policy_file().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_private(path: Path, text: str) -> None:
    """A file under the DuckTerm home: written whole, never through a symlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise KitApiError(500, f"{path} is a symlink; nothing is written through it")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _create_private(path: Path, text: str) -> None:
    """A file under the DuckTerm home that must not exist yet: created
    exclusively, never through a symlink, readable by the owner alone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _save_policies(data: dict[str, dict[str, Any]]) -> None:
    _write_private(_policy_file(), json.dumps(data, indent=2, sort_keys=True) + "\n")


def _launch_file(session_key: str) -> Path:
    name = urllib.parse.quote(session_key, safe="")
    return _teams_dir().parent / "kits" / "launches" / f"{name}.json"


def remember_launch(session_key: str, folder: str) -> None:
    """DuckTerm launched `session_key` in `folder`. This is the one record of
    where a session lives that its hooks never touch: the session row's
    repo_path and cwd follow every hook event, so a `claude` the agent starts
    in another repository moves them there."""
    _write_private(_launch_file(session_key), json.dumps({"folder": folder}) + "\n")


def launch_folder(session_key: str) -> str | None:
    try:
        data = json.loads(_launch_file(session_key).read_text())
    except (OSError, ValueError):
        return None
    folder = data.get("folder") if isinstance(data, dict) else None
    return str(folder) if folder else None


def policy_matches(policy: dict[str, Any] | None, folder: str) -> bool:
    if not policy:
        return False
    if policy.get("scope") == "all":
        return True
    return folder in [str(w) for w in policy.get("workspaces", [])]


def add_policy_workspace(kit_name: str, folder: str, kit_path: str | None = None) -> None:
    """A new team workspace made from this kit gets the kit on its first
    session. A first policy pins the kit tree the owner just read on the kit
    page (`kit_path`); without one nothing auto-installs (see apply_policies)."""
    data = policies()
    current = data.get(kit_name) or {
        "scope": "workspaces",
        "workspaces": [],
        "digest": kit_digest(_kit(kit_path).root) if kit_path else None,
    }
    if current.get("scope") == "all":
        return
    names = [str(w) for w in current.get("workspaces", [])]
    if folder not in names:
        names.append(folder)
    data[kit_name] = {**current, "scope": "workspaces", "workspaces": names}
    _save_policies(data)


def _registry_path(registry: list[dict[str, Any]], kit_name: str) -> str:
    for row in registry:
        if str(row.get("name")) == kit_name:
            return str(row.get("path"))
    raise KitApiError(404, f"no kit named {kit_name!r} is added")


def _install_where_missing(
    kit_path: str,
    kit_name: str,
    targets: list[tuple[str, str]],
    options: dict[str, str],
    replace: bool,
    fallbacks: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"installed": [], "already": [], "failed": []}
    for workspace, project in targets:
        where = {"workspace": workspace, "project": project}
        try:
            if planning.load_record(Path(project))["kits"].get(kit_name):
                out["already"].append(where)
                continue
        except (OSError, ValueError):
            pass
        try:
            install_kit(
                {
                    "kit": kit_path,
                    "project": project,
                    "harnesses": list(adapters.ADAPTERS),
                    "options": options,
                    "fallbacks": fallbacks or [],
                    "replace": replace,
                }
            )
            out["installed"].append(where)
        except KitApiError as exc:
            out["failed"].append({**where, "error": str(exc)})
    return out


def enable(
    body: dict[str, Any], registry: list[dict[str, Any]], workspaces: list[dict[str, Any]]
) -> dict[str, Any]:
    """Enable a kit for all workspaces or the named ones: the choice is kept,
    every matching repository known now is installed (for every harness),
    and a matching workspace with no repository yet gets the kit when its
    first session starts (`apply_policies`)."""
    kit_name = str(body.get("kit") or "")
    scope = str(body.get("scope") or "")
    if scope not in ("all", "workspaces"):
        raise KitApiError(400, "scope must be 'all' or 'workspaces'")
    chosen = body.get("workspaces") or []
    if not isinstance(chosen, list) or not all(isinstance(w, str) for w in chosen):
        raise KitApiError(400, "workspaces must be a list of names")
    kit_path = _registry_path(registry, kit_name)
    policy: dict[str, Any] = (
        {"scope": "all"} if scope == "all" else {"scope": "workspaces", "workspaces": chosen}
    )
    policy["digest"] = kit_digest(_kit(kit_path).root)  # the tree the review showed
    options, fallbacks = _options(body.get("options")), _fallbacks(body.get("fallbacks"))
    if options:
        policy["options"] = options
    if fallbacks:
        policy["fallbacks"] = fallbacks
    data = policies()
    data[kit_name] = policy
    _save_policies(data)
    targets = [
        (str(w.get("name", "")), str(r))
        for w in workspaces
        if policy_matches(policy, str(w.get("name", "")))
        for r in w.get("repositories", [])
    ]
    result = _install_where_missing(
        kit_path, kit_name, targets, options, bool(body.get("replace")), fallbacks
    )
    pending = [
        str(w.get("name", ""))
        for w in workspaces
        if policy_matches(policy, str(w.get("name", ""))) and not w.get("repositories")
    ]
    return {"kit": kit_name, "policy": policy, **result, "pending": pending}


def disable(body: dict[str, Any]) -> dict[str, Any]:
    """Forget the choice; installs stay where they are."""
    kit_name = str(body.get("kit") or "")
    data = policies()
    data.pop(kit_name, None)
    _save_policies(data)
    return {"kit": kit_name, "policy": None}


def apply_policies(registry: list[dict[str, Any]], folder: str, project: str) -> list[str]:
    """A session just started in `project` under workspace `folder`: install
    every enabled kit the repository lacks. Returns the kits installed."""
    if not project or not Path(project).is_dir():
        return []
    done = []
    for kit_name, policy in policies().items():
        if not policy_matches(policy, folder):
            continue
        try:
            kit_path = _registry_path(registry, kit_name)
            kit = _kit(kit_path)
        except KitApiError:
            continue
        # only the tree the owner reviewed at Enable; the catalog says "changed"
        if not policy.get("digest") or kit_digest(kit.root) != policy["digest"]:
            continue
        # the same options and fixes the owner reviewed at Enable
        result = _install_where_missing(
            kit_path,
            kit_name,
            [(folder, project)],
            _options(policy.get("options")),
            False,
            _fallbacks(policy.get("fallbacks")),
        )
        if result["installed"]:
            done.append(kit_name)
    return done


def policy_target(launch: str | None, event: dict[str, Any]) -> str | None:
    """Where a SessionStart may install enabled kits: the folder DuckTerm
    launched the session in (`launch_folder`, written by the launcher and by
    nothing else), a git root, not the home folder, and the folder the event
    says the session is in. The event's own cwd is a hook's word, never a
    target: a `claude` an agent starts in another folder inherits the session
    key and reports that folder, and the session row follows it."""
    if not launch:
        return None
    root = Path(launch)
    if not root.is_dir() or not (root / ".git").exists():
        return None
    resolved = root.resolve()
    home = Path.home().resolve()
    if resolved == home or home.is_relative_to(resolved):
        return None
    cwd = str(event.get("cwd") or "")
    if cwd and not Path(cwd).resolve().is_relative_to(resolved):
        return None
    return str(resolved)


# ── flow runs: started in a thread, watched through the run file ─────────────

_threads: dict[str, threading.Thread] = {}


class StepTracker(Protocol):
    """What the server gives a run so each harness step shows as a child
    session of the session that started the flow: `begin` is told the child
    key and what the step is and answers with the environment the step runs
    under (the DuckTerm session key and credentials); `end` is told how it
    exited. Script steps are not sessions and are not tracked."""

    def begin(self, child_key: str, info: dict[str, Any]) -> dict[str, str]:
        return {}

    def end(self, child_key: str, exit_code: int) -> None:
        return None


class TrackedExecutor(runner.Executor):
    def __init__(self, tracker: StepTracker, session: str) -> None:
        self.tracker = tracker
        self.session = session

    def run_process(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin: str | None,
        timeout: float,
    ) -> runner.ProcessResult:
        harness = {"claude": "claude-code", "codex": "codex"}.get(Path(argv[0]).name, "")
        if not harness or "SKILL_KIT_STEP" not in env:
            return super().run_process(argv, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
        child = f"{env['SKILL_KIT_RUN']}:{env['SKILL_KIT_STEP']}:{env['SKILL_KIT_ATTEMPT']}"
        extra = self.tracker.begin(
            child,
            {
                "parent": self.session,
                "flow": env.get("SKILL_KIT_FLOW", ""),
                "step": env["SKILL_KIT_STEP"],
                "attempt": env["SKILL_KIT_ATTEMPT"],
                "harness": harness,
                "cwd": str(cwd),
                "command": argv,
            },
        )
        result = super().run_process(
            argv, cwd=cwd, env={**env, **extra}, stdin=stdin, timeout=timeout
        )
        self.tracker.end(child, result.exit)
        return result


def _of_session(state: dict[str, Any], session: str | None) -> bool:
    """A session credential sees only the runs it started."""
    return not session or (state.get("executor_state") or {}).get("session") == session


def runs(project_value: Any, session: str | None = None) -> dict[str, Any]:
    project = _project(project_value)
    try:
        found = runner.list_runs(project)
    except runner.RunRefused as exc:
        raise KitApiError(400, str(exc)) from exc
    return {"project": str(project), "runs": [r for r in found if _of_session(r, session)]}


def run_detail(project_value: Any, run_id: str, session: str | None = None) -> dict[str, Any]:
    project = _project(project_value)
    try:
        run = runner.load_run(project, run_id)
    except runner.RunRefused as exc:
        raise KitApiError(404, str(exc)) from exc
    if not _of_session(run.state, session):
        raise KitApiError(404, f"no run {run_id} of this session")
    return {
        "run": run.state,
        "steps": [
            {
                "id": str(st.get("id")),
                "kind": runner.step_kind(st),
                "skill": st.get("run") or st.get("decide"),
                "question": st.get("gate"),
                "routes": st.get("routes"),
            }
            for st in run.flow.get("steps", [])
            if isinstance(st, dict)
        ],
    }


def start_run(body: dict[str, Any], tracker: StepTracker | None = None) -> dict[str, Any]:
    """Start a flow and return its run id at once; a model step takes minutes,
    so the page watches the run file rather than the request. With a tracker
    and a session, every harness step runs as a child session of it."""
    project = _project(body.get("project"))
    flow = str(body.get("flow") or "")
    if not flow:
        raise KitApiError(400, "flow is required (<kit>/<flow>)")
    harness = body.get("harness") or next(
        (h for h in planning.detect_harnesses() if h in runner.RUNNER_HARNESSES), None
    )
    if not harness:
        raise KitApiError(400, "no supported harness on PATH (claude-code, codex)")
    inputs = body.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise KitApiError(400, "inputs must be an object")
    if flow.endswith(".json"):
        raise KitApiError(400, "flow must be <kit>/<flow>, a flow installed in the project")
    # Permission flags never come over HTTP: the harness keeps its own
    # prompts (acceptEdits / --full-auto) unless the owner passes --yolo or
    # --codex-trusted to `duckterm kit run` in a terminal.
    limits = runner.Limits()
    try:
        runner.flow_path(project, flow)
    except runner.RunRefused as exc:
        raise KitApiError(400, str(exc)) from exc
    session = str(body.get("session") or "")
    executor = TrackedExecutor(tracker, session) if tracker and session else None
    started: dict[str, Any] = {}
    ready = threading.Event()

    def announce(run: runner.Run) -> None:
        started["run_id"] = run.id
        ready.set()

    def drive() -> None:
        try:
            runner.start(
                project,
                flow,
                harness=str(harness),
                inputs={str(k): str(v) for k, v in inputs.items()},
                limits=limits,
                on_start=announce,
                executor=executor,
                executor_state={"session": session} if session else None,
            )
        except runner.RunRefused as exc:
            started["error"] = str(exc)
        finally:
            ready.set()

    thread = threading.Thread(target=drive, name="kit-run", daemon=True)
    thread.start()
    ready.wait(timeout=10)
    if "error" in started and "run_id" not in started:
        raise KitApiError(409, started["error"])
    run_id = started.get("run_id")
    if not run_id:
        raise KitApiError(500, "the run did not start in time")
    _threads[run_id] = thread
    return {"run_id": run_id, "flow": flow, "harness": harness}


def answer_gate(
    body: dict[str, Any], run_id: str, tracker: StepTracker | None = None
) -> dict[str, Any]:
    project = _project(body.get("project"))
    step = str(body.get("step") or "")
    answer = str(body.get("answer") or "")
    if not step or not answer:
        raise KitApiError(400, "step and answer are required")
    limits = runner.Limits()
    try:
        run = runner.load_run(project, run_id)
    except runner.RunRefused as exc:
        raise KitApiError(404, str(exc)) from exc
    session = str((run.state.get("executor_state") or {}).get("session") or "")
    executor = TrackedExecutor(tracker, session) if tracker and session else None
    outcome: dict[str, Any] = {}

    def drive() -> None:
        try:
            runner.resume(project, run_id, gate=(step, answer), limits=limits, executor=executor)
        except runner.RunRefused as exc:
            outcome["error"] = str(exc)
        except runner.GatePending:
            pass

    thread = threading.Thread(target=drive, name="kit-resume", daemon=True)
    thread.start()
    thread.join(timeout=2)  # a refused answer surfaces now; a long step runs on
    if "error" in outcome:
        raise KitApiError(409, outcome["error"])
    _threads[run_id] = thread
    return {"run_id": run_id, "resumed": True}
