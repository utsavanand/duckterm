"""Authoring rules from the spec (§2, §3, §III.13). Parse-and-inspect only:
nothing the kit ships is executed (§III.3)."""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from duckterm.kits import adapters
from duckterm.kits import manifest as m

NEUTRAL_EVENTS = (
    "session_start",
    "session_end",
    "prompt_submit",
    "before_tool",
    "after_tool",
    "tool_failed",
    "permission_request",
    "notification",
    "stop",
    "subagent_start",
    "subagent_stop",
    "before_compact",
)
NEUTRAL_TOOLS = ("shell", "read", "write", "edit", "search")
DECISIONS = ("allow", "ask", "deny")
EFFECTS = ("read-only", "workspace", "external")
INVOCATIONS = ("auto", "user", "flow-only")
ALWAYS_KNOWLEDGE_LINE_CAP = 150
SKILL_BODY_LINE_CAP = 500
_PINNED = re.compile(r"@\d")

Report = Callable[[str, str], None]


@dataclass
class Finding:
    level: str  # "error" | "warning"
    component: str  # component id, or "manifest"
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.component}: {self.message}"


def validate(kit: m.Kit) -> list[Finding]:
    out: list[Finding] = []

    def err(component: str, message: str) -> None:
        out.append(Finding("error", component, message))

    def warn(component: str, message: str) -> None:
        out.append(Finding("warning", component, message))

    if kit.legacy:
        warn("manifest", f"{m.LEGACY_MANIFEST} is the retired name; rename to {m.MANIFEST}")
        return out
    if not m.NAME_RE.match(kit.name):
        err("manifest", f"name {kit.name!r} must match [a-z0-9-], 1-64 chars")
    if not m.SEMVER_RE.match(kit.version):
        err("manifest", f"version {kit.version!r} is not semver")
    if not kit.components:
        err("manifest", "a kit lists at least one component")
    for key in ("permissions_summary", "signature"):
        if key in kit.raw:
            warn("manifest", f"`{key}` is reserved and ignored")
    if "compatible" in kit.raw:
        warn("manifest", "`compatible` is deprecated: support is computed, not declared")
    for name, spec in kit.options.items():
        choices = spec.get("choices")
        if not isinstance(choices, list) or not choices:
            err("manifest", f"option {name!r} needs a non-empty `choices` list")
        elif "default" in spec and spec["default"] not in choices:
            err("manifest", f"option {name!r} default {spec['default']!r} is not a choice")

    ids: dict[str, m.Component] = {}
    skills = {c.name: c for c in kit.by_kind("skill")}
    mcp_names: set[str] = set()
    for c in kit.by_kind("tool"):
        mcp = c.fields.get("mcp")
        if isinstance(mcp, dict) and mcp.get("name"):
            mcp_names.add(str(mcp["name"]))

    always_lines = 0
    for c in kit.components:
        cid = kit.component_id(c)
        if c.kind not in m.KINDS:
            err(cid, f"unknown kind {c.kind!r}")
            continue
        prior = ids.get(cid)
        if prior is not None and not (c.fields.get("replaces") or prior.fields.get("replaces")):
            err(cid, "duplicate component id; use `replaces` for a harness variant")
        ids.setdefault(cid, c)
        only = c.only_harness
        if only is not None:
            for h in only:
                if h not in m.HARNESSES:
                    err(cid, f"only.harness names unknown harness {h!r}")
        for opt, choices in c.when.items():
            option = kit.options.get(opt)
            if option is None:
                err(cid, f"when.{opt}: no such option")
                continue
            for choice in choices:
                if choice not in (option.get("choices") or []):
                    err(cid, f"when.{opt}={choice!r} is not a choice of that option")
        if not m.COMPONENT_RE.match(c.name):
            err(cid, f"name {c.name!r} must match [a-z0-9_-], 1-64 chars; it becomes a path")
            continue
        replaces = c.fields.get("replaces")
        if replaces is not None and not m.NAME_RE.match(str(replaces)):
            err(cid, f"replaces {replaces!r} must be a skill name, [a-z0-9-]")
            continue
        if replaces and only is None:
            err(cid, "`replaces` needs `only` — a variant must say where it applies")

        path = c.path
        if path is not None:
            problem = _outside(kit, path)
            if problem:
                err(cid, f"path {path!r} {problem}")
                continue

        if c.kind == "knowledge":
            always_lines += _check_knowledge(kit, c, cid, err, warn)
        elif c.kind == "tool":
            _check_tool(kit, c, cid, err, warn)
        elif c.kind == "skill":
            _check_skill(kit, c, cid, err, warn, mcp_names)
        elif c.kind == "agent":
            _check_agent(kit, c, cid, err, warn)
        elif c.kind == "hook":
            _check_hook(kit, c, cid, err)
        elif c.kind == "permission":
            _check_permission(c, cid, err, warn)
        elif c.kind == "flow":
            _check_flow(kit, c, cid, err, warn, skills)
        elif c.kind == "settings":
            if c.fields.get("harness") not in m.HARNESSES:
                err(cid, "settings needs `harness` naming one harness")
            if path and not path.endswith(".json"):
                err(cid, "settings path must be a .json file")

    if always_lines > ALWAYS_KNOWLEDGE_LINE_CAP:
        err(
            "manifest",
            f"`always` knowledge totals {always_lines} lines; cap is {ALWAYS_KNOWLEDGE_LINE_CAP}"
            " — move steps into a skill, 'must always' into a hook or permission",
        )
    for team in kit.teams:
        _check_team(kit, team, err, skills)
    _check_trifecta(kit, warn)
    return out


def _outside(kit: m.Kit, rel: str) -> str:
    """Why `rel` may not be read as a kit source, or "" when it may: it has to
    stay inside the kit with no symlink anywhere on the way (a symlinked
    folder would let a kit ship files from outside its own tree)."""
    if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        return "must stay inside the kit"
    walk = kit.root
    for part in Path(rel).parts:
        walk = walk / part
        if walk.is_symlink():
            where = walk.relative_to(kit.root).as_posix()
            return f"is a symlink at {where!r}; kits ship real files"
    if walk.exists() and not walk.resolve().is_relative_to(kit.root.resolve()):
        return "resolves outside the kit"
    if not walk.exists():
        return "does not exist"
    return ""


def _check_knowledge(kit: m.Kit, c: m.Component, cid: str, err: Report, warn: Report) -> int:
    if not c.path:
        err(cid, "knowledge needs `path`")
        return 0
    text = (kit.root / c.path).read_text(errors="replace")
    load = c.fields.get("load", "always")
    if load == "always":
        return len(text.splitlines())
    if load == "on-demand":
        return 0
    if isinstance(load, dict) and isinstance(load.get("paths"), list):
        if c.fields.get("fallback") not in (None, "always"):
            err(cid, "fallback may only be 'always'")
        if adapters._every_file(load.get("paths")):
            warn(cid, "load.paths matches every file; that is load = 'always' and installs as such")
        return 0
    err(cid, f"load must be 'always', 'on-demand' or {{paths = [...]}}, not {load!r}")
    return 0


def _check_tool(kit: m.Kit, c: m.Component, cid: str, err: Report, warn: Report) -> None:
    mcp, cli = c.fields.get("mcp"), c.fields.get("cli")
    if bool(mcp) == bool(cli):
        err(cid, "a tool is exactly one of `mcp` or `cli`")
        return
    if isinstance(mcp, dict):
        if not mcp.get("name"):
            err(cid, "mcp needs a `name`")
            return
        if mcp.get("url"):
            return
        if not mcp.get("command"):
            err(cid, "mcp needs `command` (stdio) or `url`")
        args = [str(a) for a in mcp.get("args") or []]
        package = next((a for a in args if a.startswith("@") or "/" in a or "@" in a), "")
        if any(a.endswith("@latest") for a in args) or (package and not _PINNED.search(package)):
            err(cid, "mcp package must be pinned to a version (no @latest, no bare name)")
        for key, value in (mcp.get("env") or {}).items():
            if not str(value).startswith("$"):
                err(cid, f"env {key} must be a named variable like ${key}; kits never carry values")
        if not mcp.get("doctor"):
            warn(cid, "no `doctor`; the result table cannot tell logged-out from broken")
    elif mcp is not None:
        err(cid, "mcp must be a table")
    if isinstance(cli, dict):
        if not cli.get("binary"):
            err(cid, "cli needs `binary`")
            return
        if not (cli.get("doctor") or cli.get("check")):
            warn(cid, "no `doctor`; add the command that proves login")
        wrapper = cli.get("wrapper")
        if isinstance(wrapper, dict):
            command = str(wrapper.get("command", ""))
            problem = _outside(kit, command)
            if problem or not (kit.root / command).is_file():
                err(cid, f"wrapper.command {command!r} {problem or 'does not exist in the kit'}")
    elif cli is not None:
        err(cid, "cli must be a table")


def _check_skill(
    kit: m.Kit, c: m.Component, cid: str, err: Report, warn: Report, mcp_names: set[str]
) -> None:
    if not c.path:
        err(cid, "skill needs `path` to its folder")
        return
    folder = kit.root / c.path
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        err(cid, "skill folder has no SKILL.md")
        return
    text = skill_md.read_text(errors="replace")
    fm = m.frontmatter(text)
    name = fm.get("name", "")
    if name != folder.name:
        err(cid, f"SKILL.md name {name!r} must equal the folder name {folder.name!r}")
    if not m.NAME_RE.match(name or "-"):
        err(cid, "SKILL.md name must match [a-z0-9-], 1-64 chars")
    if name in ("anthropic", "claude"):
        err(cid, f"{name!r} is a reserved skill name")
    desc = fm.get("description", "")
    if not 1 <= len(desc) <= 1024:
        err(cid, "description must be 1-1024 chars")
    body = text.split("---", 2)[-1] if text.startswith("---") else text
    if len(body.splitlines()) > SKILL_BODY_LINE_CAP:
        warn(cid, f"body over {SKILL_BODY_LINE_CAP} lines; move detail into referenced files")
    if c.fields.get("effects", "workspace") not in EFFECTS:
        err(cid, f"effects must be one of {EFFECTS}")
    if c.fields.get("invocation", "auto") not in INVOCATIONS:
        err(cid, f"invocation must be one of {INVOCATIONS}")
    outputs = c.fields.get("outputs")
    if isinstance(outputs, dict):
        for oname, spec in outputs.items():
            if not isinstance(spec, dict) or not ("choice" in spec or "file" in spec):
                err(cid, f"output {oname!r} must be {{choice = [...]}} or {{file = '...'}}")
                continue
            choices = spec.get("choice")
            if "choice" in spec:
                if not isinstance(choices, list) or len(choices) < 2:
                    err(cid, f"output {oname!r} choice needs at least two values")
                else:
                    missing = [v for v in choices if str(v) not in body]
                    if missing:
                        warn(cid, f"body does not state choices verbatim: {missing}")
    elif outputs not in (None, "changes", "none"):
        err(cid, "outputs is a table of named outputs, or 'changes' / 'none'")
    for server, tool in re.findall(r"\b([a-z0-9-]+):([a-z_][a-z0-9_]*)\b", body):
        if server in ("github", "docs") and server not in mcp_names:
            err(cid, f"references MCP tool {server}:{tool} but no `tool` declares {server!r}")


def _check_agent(kit: m.Kit, c: m.Component, cid: str, err: Report, warn: Report) -> None:
    if not c.path:
        err(cid, "agent needs `path`")
        return
    fm = m.frontmatter((kit.root / c.path).read_text(errors="replace"))
    if not fm.get("name") or not fm.get("description"):
        err(cid, "agent frontmatter needs `name` and `description`")
    if "tools" not in fm:
        warn(cid, "no explicit `tools`; Copilot grants every tool when omitted")


def _check_hook(kit: m.Kit, c: m.Component, cid: str, err: Report) -> None:
    event = c.fields.get("event")
    if event not in NEUTRAL_EVENTS:
        err(cid, f"event must be one of {NEUTRAL_EVENTS}")
    match = c.fields.get("match")
    if match is not None and match not in NEUTRAL_TOOLS:
        err(cid, f"match must be one of {NEUTRAL_TOOLS}")
    command = str(c.fields.get("command") or "")
    problem = _outside(kit, command)
    if problem or not (kit.root / command).is_file():
        err(cid, f"command {command!r} {problem or 'must name a script inside the kit'}")
    timeout = c.fields.get("timeout", 5)
    if not isinstance(timeout, int) or not 1 <= timeout <= 600:
        err(cid, "timeout is seconds, 1-600")
    if event == "session_start" and c.fields.get("async"):
        err(cid, "session_start hooks are never async")


def _check_permission(c: m.Component, cid: str, err: Report, warn: Report) -> None:
    rules = c.fields.get("rules")
    if not isinstance(rules, list) or not rules:
        err(cid, "permission needs a non-empty `rules` list")
        return
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            err(cid, f"rules[{i}] must be a table")
            continue
        if rule.get("tool") not in NEUTRAL_TOOLS:
            err(cid, f"rules[{i}].tool must be one of {NEUTRAL_TOOLS}")
        if rule.get("decision") not in DECISIONS:
            err(cid, f"rules[{i}].decision must be one of {DECISIONS}")
        match = str(rule.get("match", ""))
        if not match:
            err(cid, f"rules[{i}] needs `match`")
            continue
        one_word_prefix = match.count(" ") == 0 and match.endswith("*")
        if rule.get("decision") == "allow" and (match.strip("* ") == "" or one_word_prefix):
            warn(cid, f"rules[{i}] allows {match!r}, broader than one command")


def _check_flow(
    kit: m.Kit,
    c: m.Component,
    cid: str,
    err: Report,
    warn: Report,
    skills: dict[str, m.Component],
) -> None:
    if not c.path:
        err(cid, "flow needs `path`")
        return
    try:
        flow = json.loads((kit.root / c.path).read_text())
    except json.JSONDecodeError as exc:
        err(cid, f"not valid JSON: {exc}")
        return
    steps = flow.get("steps") if isinstance(flow, dict) else None
    if not isinstance(steps, list) or not steps:
        err(cid, "flow needs a non-empty `steps` list")
        return
    ids = [str(s.get("id")) for s in steps if isinstance(s, dict)]
    members = [
        (i, str(sub.get("id")))
        for i, s in enumerate(steps)
        if isinstance(s, dict) and isinstance(s.get("parallel"), list)
        for sub in s["parallel"]
        if isinstance(sub, dict)
    ]
    if len(set(ids) | {m for _, m in members}) != len(ids) + len(members):
        err(cid, "step ids must be unique")
    declared = flow.get("inputs")
    if declared is not None and not isinstance(declared, dict):
        err(cid, "inputs must be a table of name = kind")
    for name in declared if isinstance(declared, dict) else {}:
        if not m.STEP_ID_RE.match(str(name)):
            err(cid, f"input {name!r}: name must match {m.STEP_ID_RE.pattern}")
    # a parallel member sits at its group's position: a later step may read it
    order = {sid: i for i, sid in enumerate(ids)}
    order.update({m: i for i, m in members})
    targets = set(ids) | {"end", "fail"}
    for s in steps:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id"))
        if not m.STEP_ID_RE.match(sid):
            err(cid, f"step {sid!r}: id must match {m.STEP_ID_RE.pattern} (it names a folder)")
            continue
        kinds = [k for k in ("run", "decide", "gate", "parallel", "wait", "exec") if k in s]
        if len(kinds) != 1:
            err(cid, f"step {sid}: exactly one of run / decide / gate / parallel / wait / exec")
            continue
        kind = kinds[0]
        if kind == "exec":
            problem = _outside(kit, str(s["exec"]))
            if problem:
                err(cid, f"step {sid}: exec {s['exec']!r} {problem}")
            elif not os.access(kit.root / str(s["exec"]), os.X_OK):
                err(cid, f"step {sid}: exec {s['exec']!r} is not executable")
            if s.get("effects", "read-only") not in ("read-only", "workspace", "external"):
                err(cid, f"step {sid}: effects must be read-only, workspace or external")
            if not isinstance(s.get("args", []), list):
                err(cid, f"step {sid}: args must be a list")
        if kind == "wait":
            spec = s["wait"] if isinstance(s["wait"], dict) else {}
            if spec.get("check") not in ("ci", "review"):
                err(cid, f"step {sid}: wait.check must be ci or review")
            if "timeout" not in s:
                err(cid, f"step {sid}: wait needs `timeout`")
            for outcome in ("pass", "fail", "timeout"):
                if outcome not in (s.get("routes") or {}):
                    err(cid, f"step {sid}: wait must route {outcome!r}")
        if kind in ("run", "decide"):
            skill = skills.get(str(s[kind]))
            if skill is None:
                err(cid, f"step {sid}: skill {s[kind]!r} is not in this kit")
            elif kind == "decide" and not _has_choice(skill):
                err(cid, f"step {sid}: decide needs a skill with a `choice` output")
        if kind == "parallel":
            subs = s["parallel"] if isinstance(s["parallel"], list) else []
            for sub in subs:
                mid = str(sub.get("id"))
                if not m.STEP_ID_RE.match(mid):
                    err(cid, f"step {sid}: member id {mid!r} must match {m.STEP_ID_RE.pattern}")
                skill = skills.get(str(sub.get("run") or sub.get("decide")))
                if skill is None or skill.fields.get("effects", "workspace") != "read-only":
                    err(cid, f"step {sid}: parallel steps must run read-only skills")
        routes = s.get("routes")
        edges: list[Any] = []
        if routes is not None:
            if not isinstance(routes, dict):
                err(cid, f"step {sid}: routes must be a table")
                continue
            if kind in ("run", "parallel"):
                err(cid, f"step {sid}: {kind} steps do not route; use `next`")
            if kind in ("decide", "exec") and "default" not in s:
                err(cid, f"step {sid}: {kind} with routes needs `default`")
            edges = list(routes.values())
        if s.get("next") is not None:
            if routes is not None:
                err(cid, f"step {sid}: `next` and `routes` cannot both be set")
            edges.append(s["next"])
        back = [t for t in edges if t in order and order[str(t)] <= order.get(sid, 0)]
        if back and not isinstance(s.get("max_loops"), int):
            err(cid, f"step {sid}: an edge back to {back[0]!r} needs `max_loops`")
        if not back and "max_loops" in s:
            warn(cid, f"step {sid}: max_loops without an edge back")
        for target in edges:
            if str(target) not in targets:
                err(cid, f"step {sid}: edge to unknown step {target!r}")
        for ref in _refs(s.get("with")):
            optional = ref.endswith("?")
            ref = ref[:-1] if optional else ref
            if ref.startswith("steps."):
                parts = ref.split(".")
                if len(parts) != 4 or parts[1] not in order or parts[2] != "outputs":
                    err(cid, f"step {sid}: unresolvable reference {ref!r}")
                elif not optional and order[parts[1]] >= order.get(sid, 0):
                    err(cid, f"step {sid}: reference {ref!r} may not have run yet; add `?`")
            elif ref != "workspace.diff" and not ref.startswith("inputs."):
                err(
                    cid,
                    f"step {sid}: reference {ref!r} must be steps.<id>.outputs.<name>, "
                    "inputs.<name> or workspace.diff",
                )


def _has_choice(skill: m.Component) -> bool:
    outputs = skill.fields.get("outputs")
    if not isinstance(outputs, dict):
        return False
    return any(isinstance(v, dict) and "choice" in v for v in outputs.values())


def _refs(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(v) for v in value.values() if isinstance(v, str)]
    return []


def _check_team(kit: m.Kit, team: str, err: Report, skills: dict[str, m.Component]) -> None:
    path = kit.root / team
    cid = f"{kit.name}/team/{Path(team).stem}"
    problem = _outside(kit, team)
    if problem or not path.is_file():
        err(cid, f"team template {team!r} {problem or 'does not exist'}")
        return
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        err(cid, f"not valid TOML: {exc}")
        return
    if "sessions" in data:
        err(cid, "a template has no sessions; New team creates them")
    roles = data.get("roles")
    if not isinstance(roles, dict) or not roles:
        err(cid, "template needs `roles`")
        return
    for role, spec in roles.items():
        names = spec.get("skills") or [] if isinstance(spec, dict) else []
        for skill in names:
            if str(skill) not in skills:
                err(cid, f"role {role!r} names skill {skill!r}, which this kit does not ship")


def _check_trifecta(kit: m.Kit, warn: Report) -> None:
    """Simon Willison's lethal trifecta: private data + untrusted content + a way out."""
    text = " ".join(json.dumps(c.fields) for c in kit.components).lower()
    private = any(k in text for k in ("read", "env", "secret", "token"))
    untrusted = any(k in text for k in ("url", "fetch", "search", "web"))
    exfil = any(k in text for k in ("push", "post", "send", "deploy", "publish"))
    if private and untrusted and exfil and kit.by_kind("tool"):
        warn("manifest", "private data, untrusted content and a way out together; review the tools")
