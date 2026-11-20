"""Authoring rules from the spec (§2, §3, §III.13). Parse-and-inspect only:
nothing the kit ships is executed (§III.3)."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        for opt, choice in c.when.items():
            option = kit.options.get(opt)
            if option is None:
                err(cid, f"when.{opt}: no such option")
            elif choice not in (option.get("choices") or []):
                err(cid, f"when.{opt}={choice!r} is not a choice of that option")
        if c.fields.get("replaces") and only is None:
            err(cid, "`replaces` needs `only` — a variant must say where it applies")

        path = c.path
        if path is not None:
            if ".." in Path(path).parts or Path(path).is_absolute():
                err(cid, f"path {path!r} must stay inside the kit")
                continue
            if not (kit.root / path).exists():
                err(cid, f"path {path!r} does not exist")
                continue

        if c.kind == "knowledge":
            always_lines += _check_knowledge(kit, c, cid, err)
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
            _check_flow(kit, c, cid, err, skills)
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


def _check_knowledge(kit: m.Kit, c: m.Component, cid: str, err: Report) -> int:
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
        if isinstance(wrapper, dict) and not (kit.root / str(wrapper.get("command", ""))).is_file():
            err(cid, "wrapper.command does not exist in the kit")
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
    command = c.fields.get("command")
    if not command or not (kit.root / str(command)).is_file():
        err(cid, "command must name a script inside the kit")
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
    kit: m.Kit, c: m.Component, cid: str, err: Report, skills: dict[str, m.Component]
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
    if len(set(ids)) != len(ids):
        err(cid, "step ids must be unique")
    order = {sid: i for i, sid in enumerate(ids)}
    targets = set(ids) | {"end", "fail"}
    for s in steps:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id"))
        kinds = [k for k in ("run", "decide", "gate", "parallel") if k in s]
        if len(kinds) != 1:
            err(cid, f"step {sid}: exactly one of run / decide / gate / parallel")
            continue
        kind = kinds[0]
        if kind in ("run", "decide"):
            skill = skills.get(str(s[kind]))
            if skill is None:
                err(cid, f"step {sid}: skill {s[kind]!r} is not in this kit")
            elif kind == "decide" and not _has_choice(skill):
                err(cid, f"step {sid}: decide needs a skill with a `choice` output")
        if kind == "parallel":
            subs = s["parallel"] if isinstance(s["parallel"], list) else []
            for sub in subs:
                skill = skills.get(str(sub.get("run") or sub.get("decide")))
                if skill is None or skill.fields.get("effects", "workspace") != "read-only":
                    err(cid, f"step {sid}: parallel steps must run read-only skills")
        routes = s.get("routes")
        if kind in ("decide", "gate") and routes is not None:
            if not isinstance(routes, dict):
                err(cid, f"step {sid}: routes must be a table")
                continue
            if kind == "decide" and "default" not in s:
                err(cid, f"step {sid}: decide needs `default`")
            back = [t for t in routes.values() if t in order and order[str(t)] <= order.get(sid, 0)]
            if back and not isinstance(s.get("max_loops"), int):
                err(cid, f"step {sid}: a route back to {back[0]!r} needs `max_loops`")
            for target in routes.values():
                if str(target) not in targets:
                    err(cid, f"step {sid}: route to unknown step {target!r}")
        for ref in _refs(s.get("with")):
            if ref.startswith("steps."):
                parts = ref.split(".")
                if len(parts) < 4 or parts[1] not in order or parts[2] != "outputs":
                    err(cid, f"step {sid}: unresolvable reference {ref!r}")
            elif not ref.startswith(("inputs.", "workspace.")):
                err(cid, f"step {sid}: reference {ref!r} must start with steps./inputs./workspace.")


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
    if not path.is_file():
        err(cid, f"team template {team!r} does not exist")
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
