"""Harness adapter table (§11) and where each component kind lands (§3).

An adapter never writes. It turns one component into Placements: the exact
project-relative path, the operation, the bytes or keys, and an honest status
("installed", "installed, needs trust", "not supported" with the reason,
"not yet verified" where the field rests on docs alone).
"""

from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from duckterm.kits import manifest as m

SHARED = "shared"  # a placement every chosen harness reads (AGENTS.md, .mcp.json, skills)

# Statuses from §9. Kept as plain strings so result rows read the same in the
# CLI table, the JSON and the dashboard.
INSTALLED = "installed"
NEEDS_TRUST = "installed, needs trust"
NOT_SUPPORTED = "not supported"
NOT_APPLICABLE = "not applicable"
NOT_VERIFIED = "installed, not yet verified"
OFFERED = "offered"
CHECKED = "checked"
MISSING = "missing"


@dataclass
class Placement:
    component: str
    harness: str
    kind: str
    # copy-tree | write | json-merge | toml-block | agents-block | claude-import | check | none
    op: str
    path: str = ""
    status: str = INSTALLED
    note: str = ""
    content: bytes | None = None
    source: Path | None = None
    data: dict[str, Any] | None = None  # json-merge additions; toml-block {"text": …}
    executable: bool = False

    def writes(self) -> bool:
        return self.op not in ("check", "none")


@dataclass(frozen=True)
class Adapter:
    """The §11 row. `verified_with` is the harness version a field was probed
    against; None means docs only, and placements that rest on it say so."""

    id: str
    binary: str
    verified_with: str | None
    skill_roots: tuple[str, ...]  # project roots this harness reads, preferred first
    agents_dir: str | None
    agents_format: str  # md | toml | agent-md
    reads_claude_agents: bool
    hooks_file: str | None  # project hook config, with {kit} for per-kit files
    hooks_dir: str | None  # where the kit's scripts and shim go
    hook_events: dict[str, str]  # neutral event → harness event; absent = not supported
    hook_format: str  # claude | copilot | none
    hook_blocking: str  # exit2 | copilot | none
    needs_trust: bool
    permissions: str  # settings | starlark | flags | opencode | none
    mcp_file: str | None
    mcp_format: str  # mcp-json | codex-toml | opencode-json | none
    settings_file: str | None
    knowledge_paths: str | None  # path-scoped knowledge file pattern, {kit} {name}
    knowledge_paths_key: str  # frontmatter key for the path list
    reads_agents_md: bool
    loads_agents_md_twice: bool
    rules_dir: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def installed_here(self) -> bool:
        return shutil.which(self.binary) is not None


_CLAUDE_EVENTS = {
    "session_start": "SessionStart",
    "session_end": "SessionEnd",
    "prompt_submit": "UserPromptSubmit",
    "before_tool": "PreToolUse",
    "after_tool": "PostToolUse",
    "tool_failed": "PostToolUseFailure",
    "permission_request": "PermissionRequest",
    "notification": "Notification",
    "stop": "Stop",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "before_compact": "PreCompact",
}
_CODEX_EVENTS = {
    k: v for k, v in _CLAUDE_EVENTS.items() if k not in ("tool_failed", "notification")
}
_COPILOT_EVENTS = {
    "session_start": "sessionStart",
    "session_end": "sessionEnd",
    "prompt_submit": "userPromptSubmitted",
    "before_tool": "preToolUse",
    "after_tool": "postToolUse",
    "tool_failed": "postToolUseFailure",
    "permission_request": "permissionRequest",
    "notification": "notification",
    "stop": "agentStop",
    "subagent_start": "subagentStart",
    "subagent_stop": "subagentStop",
    "before_compact": "preCompact",
}
COPILOT_EXIT2_BLOCKS = ("before_tool", "permission_request")
COPILOT_JSON_BLOCKS = ("stop", "subagent_stop")

CLAUDE_CODE = Adapter(
    id="claude-code",
    binary="claude",
    verified_with="claude-code 2.1.304",
    skill_roots=(".claude/skills",),
    agents_dir=".claude/agents",
    agents_format="md",
    reads_claude_agents=True,
    hooks_file=".claude/settings.json",
    hooks_dir=".claude/hooks/{kit}",
    hook_events=_CLAUDE_EVENTS,
    hook_format="claude",
    hook_blocking="exit2",
    needs_trust=False,
    permissions="settings",
    mcp_file=".mcp.json",
    mcp_format="mcp-json",
    settings_file=".claude/settings.json",
    knowledge_paths=".claude/rules/{kit}-{name}.md",
    knowledge_paths_key="paths",
    reads_agents_md=False,  # only through the @AGENTS.md import in CLAUDE.md
    loads_agents_md_twice=False,
)
CODEX = Adapter(
    id="codex",
    binary="codex",
    verified_with="codex 0.155.1",
    skill_roots=(".agents/skills",),
    agents_dir=".codex/agents",
    agents_format="toml",
    reads_claude_agents=False,
    hooks_file=".codex/hooks.json",
    hooks_dir=".codex/hooks/{kit}",
    hook_events=_CODEX_EVENTS,
    hook_format="claude",
    hook_blocking="exit2",
    needs_trust=True,
    permissions="starlark",
    mcp_file=".codex/config.toml",
    mcp_format="codex-toml",
    settings_file=".codex/config.toml",
    knowledge_paths=None,
    knowledge_paths_key="",
    reads_agents_md=True,
    loads_agents_md_twice=False,
    rules_dir=".codex/rules",
    extra={"hook_timeout_cap": 3, "agents_md_cap": 32 * 1024},
)
COPILOT = Adapter(
    id="copilot",
    binary="copilot",
    verified_with="copilot 1.0.90",
    skill_roots=(".github/skills", ".agents/skills", ".claude/skills"),
    agents_dir=".github/agents",
    agents_format="agent-md",
    reads_claude_agents=True,
    hooks_file=".github/hooks/{kit}.json",
    hooks_dir=".github/hooks/{kit}",
    hook_events=_COPILOT_EVENTS,
    hook_format="copilot",
    hook_blocking="copilot",
    needs_trust=False,
    permissions="flags",
    mcp_file=".mcp.json",
    mcp_format="mcp-json",
    settings_file=None,
    knowledge_paths=".github/instructions/{kit}-{name}.instructions.md",
    knowledge_paths_key="applyTo",
    reads_agents_md=True,
    loads_agents_md_twice=True,
)
OPENCODE = Adapter(
    id="opencode",
    binary="opencode",
    verified_with=None,
    skill_roots=(".opencode/skills", ".claude/skills", ".agents/skills"),
    agents_dir=".opencode/agents",
    agents_format="md",
    reads_claude_agents=False,
    hooks_file=None,
    hooks_dir=None,
    hook_events={},
    hook_format="none",
    hook_blocking="none",
    needs_trust=False,
    permissions="opencode",
    mcp_file="opencode.json",
    mcp_format="opencode-json",
    settings_file="opencode.json",
    knowledge_paths=None,
    knowledge_paths_key="",
    reads_agents_md=True,
    loads_agents_md_twice=False,
)
ADAPTERS: dict[str, Adapter] = {a.id: a for a in (CLAUDE_CODE, CODEX, COPILOT, OPENCODE)}

# Neutral tool names (§3.5) → each harness's own.
CLAUDE_TOOL = {
    "shell": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit|MultiEdit",
    "search": "Grep|Glob",
}
CLAUDE_PERMISSION_TOOL = {"shell": "Bash", "read": "Read", "write": "Write", "edit": "Edit"}
OPENCODE_TOOL = {
    "shell": "bash",
    "read": "read",
    "write": "write",
    "edit": "edit",
    "search": "grep",
}
COPILOT_TOOL = {"shell": "shell", "write": "write", "read": "read", "edit": "write"}

SHIM_NAME = "_skill_kit_shim.py"
ATTR_OPEN = "<!-- skill-kit {kit}:{name} v{version} -->"
ATTR_CLOSE = "<!-- /skill-kit {kit}:{name} -->"
IMPORT_OPEN = "<!-- skill-kit: import -->"
IMPORT_CLOSE = "<!-- /skill-kit: import -->"


def skill_roots_for(chosen: list[str]) -> dict[str, list[str]]:
    """The smallest set of project roots that covers every chosen harness
    (greedy set cover over the preference order), and which harnesses each
    root serves. Copilot and OpenCode read several roots, so one copy can
    serve three harnesses (probe P1)."""
    remaining = set(chosen)
    chosen_roots: dict[str, list[str]] = {}
    candidates = [".claude/skills", ".agents/skills", ".github/skills", ".opencode/skills"]
    while remaining:
        best = max(
            candidates, key=lambda r: len([h for h in remaining if r in ADAPTERS[h].skill_roots])
        )
        served = [h for h in remaining if best in ADAPTERS[h].skill_roots]
        if not served:
            break
        chosen_roots[best] = served
        remaining -= set(served)
        candidates.remove(best)
    return chosen_roots


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


# ── per kind ────────────────────────────────────────────────────────────────


def place_knowledge(
    kit: m.Kit,
    c: m.Component,
    chosen: list[str],
    roots: dict[str, list[str]],
    force_fallback: bool = False,
) -> list[Placement]:
    """`force_fallback` is the owner's Fix from the review: load a path-scoped
    file always on the harnesses that cannot scope by path, as if the kit had
    said `fallback = "always"`; the kit itself is not edited."""
    cid = kit.component_id(c)
    text = (kit.root / str(c.path)).read_text()
    load = c.fields.get("load", "always")
    every_file = isinstance(load, dict) and _every_file(load.get("paths"))
    if every_file:
        load = "always"
    fallback = force_fallback or c.fields.get("fallback") == "always"
    fell_back = isinstance(load, dict) and fallback and _needs_fallback(chosen)
    out: list[Placement] = []
    if load == "always" or fell_back:
        out.append(
            Placement(
                cid,
                SHARED,
                "knowledge",
                "agents-block",
                "AGENTS.md",
                content=text.encode(),
                data={"name": c.name, "version": kit.version},
            )
        )
        if "claude-code" in chosen:
            out.append(
                Placement(
                    cid,
                    "claude-code",
                    "knowledge",
                    "claude-import",
                    "CLAUDE.md",
                    note="CLAUDE.md gains an attributed @AGENTS.md import; nothing else is touched",
                )
            )
        if "copilot" in chosen:
            out.append(
                Placement(
                    cid,
                    "copilot",
                    "knowledge",
                    "none",
                    "AGENTS.md",
                    status=INSTALLED,
                    note="Copilot loads AGENTS.md and CLAUDE.md both: the text loads twice (P4)",
                )
            )
        if "codex" in chosen:
            out.append(
                Placement(
                    cid, "codex", "knowledge", "none", "AGENTS.md", note="read directly; 32 KiB cap"
                )
            )
        if "opencode" in chosen:
            out.append(
                Placement(
                    cid,
                    "opencode",
                    "knowledge",
                    "none",
                    "AGENTS.md",
                    status=NOT_VERIFIED,
                    note="docs: AGENTS.md preferred over CLAUDE.md",
                )
            )
        if every_file or fell_back:
            # AGENTS.md is read by every harness, so one harness without
            # path scoping makes the text always-on for all of them.
            why = (
                "paths match every file, so this loads always"
                if every_file
                else ", ".join(h for h in chosen if ADAPTERS[h].knowledge_paths is None)
                + " cannot scope by path, so this loads always, for every harness, from AGENTS.md"
            )
            for p in out:
                p.note = (p.note + "; " if p.note else "") + why
        return out
    if isinstance(load, dict):
        paths = [str(p) for p in load.get("paths") or []]
        for h in chosen:
            a = ADAPTERS[h]
            if a.knowledge_paths is None:
                out.append(
                    Placement(
                        cid,
                        h,
                        "knowledge",
                        "none",
                        status=NOT_SUPPORTED,
                        note=f"{h}: no path-scoped instructions (P10); Fix loads it always there",
                    )
                )
                continue
            front = (
                f"---\n{a.knowledge_paths_key}:\n"
                + "".join(f"  - {json.dumps(p)}\n" for p in paths)
                + "---\n"
            )
            out.append(
                Placement(
                    cid,
                    h,
                    "knowledge",
                    "write",
                    a.knowledge_paths.format(kit=kit.name, name=c.name),
                    content=(front + text).encode(),
                )
            )
        return out
    if load == "on-demand":
        fm_desc = c.fields.get("description") or text.strip().splitlines()[0].lstrip("# ").strip()
        skill = f"---\nname: {c.name}\ndescription: {fm_desc}\n---\n{text}"
        for root, served in roots.items():
            out.append(
                Placement(
                    cid,
                    SHARED,
                    "knowledge",
                    "write",
                    f"{root}/{c.name}/SKILL.md",
                    content=skill.encode(),
                    note=f"reference skill for {', '.join(served)}",
                )
            )
        return out
    return [
        Placement(
            cid, SHARED, "knowledge", "none", status=NOT_SUPPORTED, note=f"unknown load {load!r}"
        )
    ]


def _needs_fallback(chosen: list[str]) -> bool:
    return any(ADAPTERS[h].knowledge_paths is None for h in chosen)


EVERY_FILE = {"*", "**", "**/*", "./**", "./**/*"}


def _every_file(paths: object) -> bool:
    """A path scope with a glob that names every file is `always` under
    another name: the globs are alternatives, so one universal member covers
    everything the others would."""
    return bool(paths) and isinstance(paths, list) and any(str(p) in EVERY_FILE for p in paths)


def place_skill(
    kit: m.Kit, c: m.Component, chosen: list[str], roots: dict[str, list[str]]
) -> list[Placement]:
    cid = kit.component_id(c)
    source = kit.root / str(c.path)
    name = c.fields.get("replaces") or source.name
    out: list[Placement] = []
    for root, served in roots.items():
        variant_ok = [h for h in served if h in c.only_harness] if c.only_harness else served
        if not variant_ok:
            continue
        out.append(
            Placement(
                cid,
                SHARED,
                "skill",
                "copy-tree",
                f"{root}/{name}",
                source=source,
                note=f"read by {', '.join(variant_ok)}",
            )
        )
    invocation = c.fields.get("invocation", "auto")
    if "codex" in chosen and invocation != "auto":
        out.append(
            Placement(
                cid,
                "codex",
                "skill",
                "none",
                status=INSTALLED,
                note=f"invocation '{invocation}' not enforced; Codex reads name/description (P3)",
            )
        )
    if "codex" in chosen and c.fields.get("effects") == "external":
        out.append(
            Placement(
                cid,
                "codex",
                "skill",
                "none",
                status=INSTALLED,
                note="effects=external: add a before_tool guard; Codex cannot restrict invocation",
            )
        )
    if "opencode" in chosen and len(roots) > 1:
        out.append(
            Placement(
                cid,
                "opencode",
                "skill",
                "none",
                status=NOT_VERIFIED,
                note="skill present in two roots; OpenCode's duplicate handling is unverified (P1)",
            )
        )
    return out


def place_agent(kit: m.Kit, c: m.Component, chosen: list[str]) -> list[Placement]:
    cid = kit.component_id(c)
    source = kit.root / str(c.path)
    text = source.read_text()
    fm = m.frontmatter(text)
    name = fm.get("name") or source.stem
    body = text.split("---", 2)[-1].lstrip("\n") if text.startswith("---") else text
    out: list[Placement] = []
    for h in chosen:
        a = ADAPTERS[h]
        if h == "copilot" and "claude-code" in chosen:
            out.append(
                Placement(
                    cid,
                    h,
                    "agent",
                    "none",
                    ".claude/agents",
                    note="Copilot reads .claude/agents natively",
                )
            )
            continue
        if a.agents_format == "toml":
            toml = (
                f"name = {json.dumps(name)}\n"
                f'description = {json.dumps(fm.get("description", ""))}\n'
                f'developer_instructions = """\n{body.rstrip()}\n"""\n'
            )
            out.append(
                Placement(
                    cid,
                    h,
                    "agent",
                    "write",
                    f"{a.agents_dir}/{name}.toml",
                    content=toml.encode(),
                    note=(
                        "tools/model keys have no Codex form; ignored"
                        if fm.get("tools") or fm.get("model")
                        else ""
                    ),
                )
            )
        elif a.agents_format == "agent-md":
            out.append(
                Placement(
                    cid,
                    h,
                    "agent",
                    "write",
                    f"{a.agents_dir}/{name}.agent.md",
                    content=text.encode(),
                )
            )
        elif h == "opencode":
            front = (
                "---\n"
                + "".join(f"{k}: {v}\n" for k, v in fm.items() if k not in ("tools",))
                + "mode: subagent\n---\n"
            )
            out.append(
                Placement(
                    cid,
                    h,
                    "agent",
                    "write",
                    f"{a.agents_dir}/{name}.md",
                    content=(front + body).encode(),
                    status=NOT_VERIFIED,
                    note="tools dropped: OpenCode tool names differ",
                )
            )
        else:
            out.append(
                Placement(
                    cid, h, "agent", "write", f"{a.agents_dir}/{name}.md", content=text.encode()
                )
            )
    return out


def shim_command(a: Adapter, kit: m.Kit, event: str, script_rel: str) -> str:
    assert a.hooks_dir is not None
    shim = f"{a.hooks_dir.format(kit=kit.name)}/{SHIM_NAME}"
    return shlex.join(["python3", shim, a.id, event, kit.name, script_rel])


def place_hook(
    kit: m.Kit, c: m.Component, chosen: list[str], shim_source: bytes
) -> list[Placement]:
    cid = kit.component_id(c)
    event = str(c.fields["event"])
    match = c.fields.get("match")
    script = Path(str(c.fields["command"]))
    blocking = bool(c.fields.get("blocking"))
    timeout = int(c.fields.get("timeout", 5))
    out: list[Placement] = []
    for h in chosen:
        a = ADAPTERS[h]
        if a.hook_format == "none":
            out.append(
                Placement(
                    cid,
                    h,
                    "hook",
                    "none",
                    status=NOT_SUPPORTED,
                    note="OpenCode hooks are JS plugins only",
                )
            )
            continue
        harness_event = a.hook_events.get(event)
        if harness_event is None:
            out.append(
                Placement(
                    cid, h, "hook", "none", status=NOT_SUPPORTED, note=f"{h} has no {event} event"
                )
            )
            continue
        note = ""
        if h == "copilot" and blocking and event not in COPILOT_EXIT2_BLOCKS + COPILOT_JSON_BLOCKS:
            out.append(
                Placement(
                    cid,
                    h,
                    "hook",
                    "none",
                    status=NOT_SUPPORTED,
                    note=f"blocking refused: Copilot treats exit 2 on {harness_event} as a warning",
                )
            )
            continue
        if h == "copilot" and blocking and event in COPILOT_JSON_BLOCKS:
            note = "blocks through JSON decision, which the shim emits"
        if h == "copilot":
            note = (note + "; " if note else "") + "timeouts fail open in Copilot"
        assert a.hooks_dir is not None and a.hooks_file is not None
        hooks_dir = a.hooks_dir.format(kit=kit.name)
        script_rel = f"{hooks_dir}/{script.name}"
        out.append(
            Placement(
                cid,
                h,
                "hook",
                "write",
                f"{hooks_dir}/{SHIM_NAME}",
                content=shim_source,
                executable=True,
                note="one shim per kit; normalises the payload for the kit's script",
            )
        )
        out.append(
            Placement(
                cid,
                h,
                "hook",
                "write",
                script_rel,
                content=(kit.root / script).read_bytes(),
                executable=True,
            )
        )
        command = shim_command(a, kit, event, script_rel)
        effective_timeout = min(timeout, a.extra.get("hook_timeout_cap", timeout))
        if effective_timeout != timeout:
            note = (
                note + "; " if note else ""
            ) + f"timeout clamped to {effective_timeout}s (Codex cap)"
        if a.hook_format == "claude":
            entry: dict[str, Any] = {
                "type": "command",
                "command": command,
                "timeout": effective_timeout,
            }
            if blocking and h == "claude-code":
                entry["onFailure"] = "block"
            group: dict[str, Any] = {"hooks": [entry]}
            if match and event in (
                "before_tool",
                "after_tool",
                "tool_failed",
                "permission_request",
            ):
                group["matcher"] = CLAUDE_TOOL[str(match)]
            data: dict[str, Any] = {"hooks": {harness_event: [group]}}
        else:
            entry = {"type": "command", "command": command, "timeoutSec": effective_timeout}
            data = {"version": 1, "hooks": {harness_event: [entry]}}
        status = NEEDS_TRUST if a.needs_trust else INSTALLED
        if a.needs_trust:
            note = (
                (note + "; " if note else "")
                + "Codex runs project hooks only in a trusted project; DuckTerm never writes trust"
            )
        out.append(
            Placement(
                cid,
                h,
                "hook",
                "json-merge",
                a.hooks_file.format(kit=kit.name),
                data=data,
                status=status,
                note=note,
            )
        )
    return out


def copilot_flags(kit: m.Kit) -> list[str]:
    """`--allow-tool`/`--deny-tool` for every permission rule of the kit that
    Copilot can take; `ask` rules are Copilot's default and pass nothing."""
    flags: list[str] = []
    for c in kit.components:
        if c.kind != "permission":
            continue
        for r in c.fields.get("rules", []):
            if not isinstance(r, dict):
                continue
            tool = COPILOT_TOOL.get(str(r.get("tool")))
            match = str(r.get("match", ""))
            if tool is None or r.get("decision") == "ask" or any(ch in match for ch in ")\n,"):
                continue  # validate refuses such a match; never let one read as two rules
            flag = "--allow-tool" if r.get("decision") == "allow" else "--deny-tool"
            flags += [flag, f"{tool}({match})"]
    return flags


def place_permission(kit: m.Kit, c: m.Component, chosen: list[str]) -> list[Placement]:
    cid = kit.component_id(c)
    rules = [r for r in c.fields.get("rules", []) if isinstance(r, dict)]
    out: list[Placement] = []
    for h in chosen:
        a = ADAPTERS[h]
        if a.permissions == "settings":
            perms: dict[str, list[str]] = {"allow": [], "ask": [], "deny": []}
            unmapped: list[dict[str, Any]] = []
            for r in rules:
                tool = CLAUDE_PERMISSION_TOOL.get(str(r["tool"]))
                if tool is None:
                    unmapped.append(r)
                    continue
                perms[str(r["decision"])].append(f"{tool}({r['match']})")
            note = f"{len(unmapped)} search rule(s) have no Claude Code form" if unmapped else ""
            out.append(
                Placement(
                    cid,
                    h,
                    "permission",
                    "json-merge",
                    a.settings_file or "",
                    data={"permissions": {k: v for k, v in perms.items() if v}},
                    note=note,
                )
            )
        elif a.permissions == "starlark":
            lines = [f"# skill-kit {kit.name} v{kit.version}: generated, do not edit"]
            skipped = 0
            for r in rules:
                if r["tool"] != "shell":
                    skipped += 1
                    continue
                tokens = [t for t in shlex.split(str(r["match"]).replace("*", " ")) if t]
                decision = {"allow": "allow", "ask": "prompt", "deny": "forbidden"}[
                    str(r["decision"])
                ]
                lines.append(
                    f"prefix_rule(pattern={json.dumps(tokens)}, decision={json.dumps(decision)})"
                )
            note = "shell prefixes only; " + (
                f"{skipped} read/write/edit rule(s) not supported in Codex (P9)"
                if skipped
                else "verified with codex execpolicy check"
            )
            out.append(
                Placement(
                    cid,
                    h,
                    "permission",
                    "write",
                    f"{a.rules_dir}/{kit.name}.rules",
                    content=("\n".join(lines) + "\n").encode(),
                    note=note,
                )
            )
        elif a.permissions == "flags":
            flags = []
            for r in rules:
                tool = COPILOT_TOOL.get(str(r["tool"]))
                if tool is None or r["decision"] == "ask":
                    continue
                flag = "--allow-tool" if r["decision"] == "allow" else "--deny-tool"
                flags.append(f"{flag} '{tool}({r['match']})'")
            # Copilot takes permissions as command-line flags; DuckTerm launches
            # Copilot, so it passes them (copilot_flags) for a repository whose
            # record holds this kit. Nothing is written for it.
            out.append(
                Placement(
                    cid,
                    h,
                    "permission",
                    "none",
                    status=INSTALLED,
                    note="passed as flags when DuckTerm launches Copilot; ask is the default: "
                    + " ".join(flags),
                )
            )
        elif a.permissions == "opencode":
            perm: dict[str, dict[str, str]] = {}
            for r in rules:
                perm.setdefault(OPENCODE_TOOL[str(r["tool"])], {})[str(r["match"])] = str(
                    r["decision"]
                )
            out.append(
                Placement(
                    cid,
                    h,
                    "permission",
                    "json-merge",
                    a.settings_file or "",
                    data={"permission": perm},
                    status=NOT_VERIFIED,
                    note="format from docs (P9); last match wins",
                )
            )
    return out


def place_tool(kit: m.Kit, c: m.Component, chosen: list[str]) -> list[Placement]:
    cid = kit.component_id(c)
    mcp, cli = c.fields.get("mcp"), c.fields.get("cli")
    out: list[Placement] = []
    if isinstance(cli, dict):
        binary = str(cli["binary"])
        found = shutil.which(binary)
        doctor = cli.get("doctor") or cli.get("check") or ""
        note = (
            f"doctor: {doctor} (run it yourself after install; never run by validate)"
            if doctor
            else ""
        )
        if not found:
            note = f"install: {cli.get('install_hint', '?')}; " + note
        out.append(
            Placement(
                cid,
                SHARED,
                "tool",
                "check",
                binary,
                status=CHECKED if found else MISSING,
                note=note,
            )
        )
        return out
    assert isinstance(mcp, dict)
    name = str(mcp["name"])
    env = {str(k): str(v) for k, v in (mcp.get("env") or {}).items()}
    done_shared = False
    for h in chosen:
        a = ADAPTERS[h]
        if a.mcp_format == "mcp-json":
            if done_shared:
                out.append(Placement(cid, h, "tool", "none", ".mcp.json", note="shares .mcp.json"))
                continue
            done_shared = True
            server: dict[str, Any] = (
                {"type": "http", "url": mcp["url"]}
                if mcp.get("url")
                else {"command": mcp["command"], "args": [str(x) for x in mcp.get("args") or []]}
            )
            if env and not mcp.get("url"):
                server["env"] = {k: "${" + v.lstrip("$") + "}" for k, v in env.items()}
            out.append(
                Placement(
                    cid,
                    SHARED,
                    "tool",
                    "json-merge",
                    a.mcp_file or "",
                    data={"mcpServers": {name: server}},
                    note="credentials by name: " + ", ".join(env.values()) if env else "",
                )
            )
        elif a.mcp_format == "codex-toml":
            lines = [f"[mcp_servers.{name}]"]
            if mcp.get("url"):
                lines.append(f"url = {json.dumps(mcp['url'])}")
            else:
                lines.append(f"command = {json.dumps(mcp['command'])}")
                lines.append(f"args = {json.dumps([str(x) for x in mcp.get('args') or []])}")
            note = ""
            if env:
                note = (
                    "env not written: Codex passes env values literally; export "
                    + ", ".join(v.lstrip("$") for v in env.values())
                    + " in your shell"
                )
            out.append(
                Placement(
                    cid,
                    h,
                    "tool",
                    "toml-block",
                    a.mcp_file or "",
                    data={"text": "\n".join(lines) + "\n"},
                    note=note,
                )
            )
        elif a.mcp_format == "opencode-json":
            server = (
                {"type": "remote", "url": mcp["url"]}
                if mcp.get("url")
                else {
                    "type": "local",
                    "command": [str(mcp["command"])] + [str(x) for x in mcp.get("args") or []],
                }
            )
            if env and not mcp.get("url"):
                server["environment"] = {k: "{env:" + v.lstrip("$") + "}" for k, v in env.items()}
            out.append(
                Placement(
                    cid,
                    h,
                    "tool",
                    "json-merge",
                    a.mcp_file or "",
                    data={"mcp": {name: server}},
                    status=NOT_VERIFIED,
                    note="format from docs",
                )
            )
    return out


def place_settings(kit: m.Kit, c: m.Component, chosen: list[str]) -> list[Placement]:
    cid = kit.component_id(c)
    harness = str(c.fields.get("harness"))
    out: list[Placement] = []
    data = json.loads((kit.root / str(c.path)).read_text())
    for h in chosen:
        if h != harness:
            out.append(
                Placement(cid, h, "settings", "none", status=NOT_APPLICABLE, note=f"for {harness}")
            )
            continue
        a = ADAPTERS[h]
        if a.settings_file is None:
            out.append(
                Placement(
                    cid,
                    h,
                    "settings",
                    "none",
                    status=NOT_SUPPORTED,
                    note=f"{h} has no project settings file",
                )
            )
        elif a.settings_file.endswith(".toml"):
            lines = [f"{k} = {json.dumps(v)}" for k, v in data.items() if not isinstance(v, dict)]
            out.append(
                Placement(
                    cid,
                    h,
                    "settings",
                    "toml-block",
                    a.settings_file,
                    data={"text": "\n".join(lines) + "\n"},
                    note="top-level keys only; tables need their own component",
                )
            )
        else:
            out.append(Placement(cid, h, "settings", "json-merge", a.settings_file, data=data))
    return out


def _flow_shortcut(kit: m.Kit, flow: str, spec: dict[str, Any]) -> bytes:
    """A Claude Code project command, `/<flow>`, that starts the flow from the
    session it is typed in. The command runs `duckterm kit run`, which hands
    the run to the DuckTerm server when a session credential is present, so
    the steps show as child sessions; the agent only relays the output."""
    raw = spec.get("inputs")
    # input names and the description come from the kit; both land in a file
    # Claude Code reads as frontmatter, so names are identifiers (validate
    # flags others) and the description is one quoted line
    names = sorted(raw) if isinstance(raw, dict) else []
    inputs = [n for n in names if m.STEP_ID_RE.match(str(n))]
    fallback = f"Start the {flow} flow of {kit.name}"
    desc = " ".join(str(spec.get("description") or fallback).split())
    start = f"duckterm kit run {kit.name}/{flow}"
    argv = start + "".join(f" --input {name}=<{name}>" for name in inputs)
    lines = [
        "---",
        f"description: {json.dumps(desc)}",
        # this flow's start only, never --gate or --yolo: exact when the flow takes
        # no inputs, a prefix (for the --input pairs) when it does
        f"allowed-tools: Bash({start}{':*' if inputs else ''})",
        "---",
        f"Start the `{kit.name}/{flow}` flow of the {kit.name} skill kit from this session:",
        "",
        f"    {argv}",
        "",
    ]
    if inputs:
        lines += [
            "Fill each `<input>` from the arguments: $ARGUMENTS. Ask for any input"
            " the arguments do not give.",
            "",
        ]
    lines += [
        "Relay what the command prints: the run id and each step as it finishes."
        " A gate is answered by the owner in DuckTerm (the session's Flows tab),"
        " not from here. Do not do the steps yourself; they run as child sessions"
        " of this one.",
        "",
    ]
    return "\n".join(lines).encode()


def place_flow(kit: m.Kit, c: m.Component, active: list[str]) -> list[Placement]:
    """The flow file, plus what the runner needs beside it: `skills.json`
    (every skill's declared outputs and effects, every exec script's hash)
    and the scripts flows `exec`. Shared across flows; the planner dedupes.
    For Claude Code, also the `/<flow>` command the kit page advertises."""
    from duckterm.kits import runner

    cid = kit.component_id(c)
    source = kit.root / str(c.path)
    flows = []
    for f in kit.by_kind("flow"):
        try:
            flows.append(json.loads((kit.root / str(f.path)).read_text()))
        except (OSError, ValueError):
            continue
    manifest = runner.skills_manifest(kit, flows, kit.root)
    folder = f"{runner.FLOWS}/{kit.name}"
    run_name = f"{kit.name}/{source.stem}"
    try:
        spec = json.loads(source.read_text())
    except (OSError, ValueError):
        spec = {}
    out = [
        Placement(
            cid,
            SHARED,
            "flow",
            "write",
            f"{folder}/{source.name}",
            content=source.read_bytes(),
            note=f"/{source.stem} in a Claude Code session, or `duckterm kit run {run_name}`",
        ),
    ]
    if "claude-code" in active:
        out.append(
            Placement(
                cid,
                "claude-code",
                "flow",
                "write",
                f".claude/commands/{source.stem}.md",
                content=_flow_shortcut(kit, source.stem, spec if isinstance(spec, dict) else {}),
                note=f"the /{source.stem} shortcut",
            )
        )
    out += [
        Placement(
            cid,
            SHARED,
            "flow",
            "write",
            f"{folder}/{runner.SKILLS_FILE}",
            content=(json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        ),
    ]
    for rel in sorted(manifest["scripts"]):
        out.append(
            Placement(
                cid,
                SHARED,
                "flow",
                "write",
                f"{folder}/{rel}",
                content=(kit.root / rel).read_bytes(),
                executable=True,
            )
        )
    return out


def place_team(kit: m.Kit, team: str) -> Placement:
    name = Path(team).stem
    return Placement(
        f"{kit.name}/team/{name}",
        SHARED,
        "team",
        "none",
        team,
        status=OFFERED,
        note="a team template; New team instantiates it into a workspace, install never does",
    )
