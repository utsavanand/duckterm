"""What the Skill kits page asks the server for, as plain functions over the
kit engine: the installed kits of a project with their procedures and flows,
a dry-run plan for a kit folder, install, uninstall, verify, and flow runs.
The server's handlers parse the request and call these; the CLI uses the same
engine underneath, so the page and the terminal cannot disagree."""

from __future__ import annotations

import json
import threading
import tomllib
from pathlib import Path
from typing import Any

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


def plan_for(body: dict[str, Any]) -> dict[str, Any]:
    kit = _kit(body.get("kit"))
    project = _project(body.get("project"))
    harnesses = _harnesses(body.get("harnesses"))
    try:
        p = planning.build(kit, project, harnesses, _options(body.get("options")))
    except ValueError as exc:
        raise KitApiError(400, str(exc)) from exc
    return {"inventory": inventory(kit), "plan": p.to_json()}


def install_kit(body: dict[str, Any]) -> dict[str, Any]:
    kit = _kit(body.get("kit"))
    project = _project(body.get("project"))
    harnesses = _harnesses(body.get("harnesses"))
    try:
        p = planning.build(kit, project, harnesses, _options(body.get("options")))
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


# ── flow runs: started in a thread, watched through the run file ─────────────

_threads: dict[str, threading.Thread] = {}


def runs(project_value: Any) -> dict[str, Any]:
    project = _project(project_value)
    return {"project": str(project), "runs": runner.list_runs(project)}


def run_detail(project_value: Any, run_id: str) -> dict[str, Any]:
    project = _project(project_value)
    try:
        run = runner.load_run(project, run_id)
    except runner.RunRefused as exc:
        raise KitApiError(404, str(exc)) from exc
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


def start_run(body: dict[str, Any]) -> dict[str, Any]:
    """Start a flow and return its run id at once; a model step takes minutes,
    so the page watches the run file rather than the request."""
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
    limits = runner.Limits(
        yolo=bool(body.get("yolo")), codex_trusted=bool(body.get("codex_trusted"))
    )
    try:
        runner.flow_path(project, flow)
    except runner.RunRefused as exc:
        raise KitApiError(400, str(exc)) from exc
    session = str(body.get("session") or "")
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


def answer_gate(body: dict[str, Any], run_id: str) -> dict[str, Any]:
    project = _project(body.get("project"))
    step = str(body.get("step") or "")
    answer = str(body.get("answer") or "")
    if not step or not answer:
        raise KitApiError(400, "step and answer are required")
    limits = runner.Limits(
        yolo=bool(body.get("yolo")), codex_trusted=bool(body.get("codex_trusted"))
    )
    try:
        runner.load_run(project, run_id)
    except runner.RunRefused as exc:
        raise KitApiError(404, str(exc)) from exc
    outcome: dict[str, Any] = {}

    def drive() -> None:
        try:
            runner.resume(project, run_id, gate=(step, answer), limits=limits)
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
