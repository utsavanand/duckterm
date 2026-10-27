"""Skill kits: collections of skills, hooks, sub-agents, guardrails and
personas that agents in a project pick up — uv-suite is the canonical example.
Distinct from the runtime ADAPTERS in harnesses.py, which teach Duckterm how to
drive/observe one agent CLI.

A kit is registered by its directory, which must carry a `duckterm-harness.json`:

    {
      "name": "uv-suite",
      "description": "Agents, skills, hooks, and guardrails for Claude Code",
      "detect": ".claude/skills/uv-help",                 // optional
      "compatible": ["claude-code"]                       // optional
    }

- `detect` is a path (relative to a project) whose existence proves the skill
  kit is installed there — how the dashboard shows which skill kit a session
  is running under.
- `compatible` names the agent harnesses the kit works with (registry names
  like "claude-code"); omitted means it doesn't say.

Duckterm never runs a kit's own scripts (owner decision 2026-10-04): older
manifests' `install`, `uninstall` and `args_choices` keys are ignored. Installs
come back with the declarative component format.
"""

import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST = "duckterm-harness.json"


@dataclass
class Suite:
    name: str
    description: str
    path: Path
    detect: str | None
    compatible: list[str]


def load(path: Path) -> Suite:
    """Read a kit from its directory. Raises ValueError if it has no manifest."""
    manifest = path / MANIFEST
    if not manifest.is_file():
        raise ValueError(f"{path} has no {MANIFEST}")
    data = json.loads(manifest.read_text())
    return Suite(
        name=str(data.get("name") or path.name),
        description=str(data.get("description") or ""),
        path=path,
        detect=str(data["detect"]) if data.get("detect") else None,
        compatible=[str(c) for c in data.get("compatible") or []],
    )


# What a suite ships, by where such things conventionally live in its tree.
# (kind, glob) — globs stay specific so a registered suite with a node_modules
# doesn't get walked.
_CONTENT_GLOBS = [
    ("skill", "skills/*/SKILL.md"),
    ("skill", "*/skills/*/SKILL.md"),
    ("sub-agent", "agents/*.md"),
    ("sub-agent", "agents/*/*.md"),
    ("hook", "hooks/*.sh"),
    ("hook", "*/hooks/*.sh"),
    ("guardrail", "guardrails/*.md"),
    ("persona", "personas/*.json"),
]


def contents(suite: Suite, cap: int = 200) -> list[dict[str, str]]:
    """What ships inside a suite — skills, sub-agents, hooks, guardrails,
    personas — each with the one-liner its own file declares (frontmatter
    `description:` for markdown, the first header comment for shell)."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for kind, pattern in _CONTENT_GLOBS:
        for path in sorted(suite.path.glob(pattern)):
            name = path.parent.name if path.name == "SKILL.md" else path.stem
            if (kind, name) in seen:
                continue
            seen.add((kind, name))
            out.append({"kind": kind, "name": name, "description": _describe(path)})
            if len(out) >= cap:
                return out
    return out


def _describe(path: Path) -> str:
    try:
        head = path.read_text(errors="replace")[:4000].splitlines()
    except OSError:
        return ""
    if path.suffix == ".md":
        in_front = False
        for i, line in enumerate(head):
            stripped = line.strip()
            if stripped == "---":
                in_front = not in_front
                continue
            if in_front and stripped.startswith("description:"):
                value = stripped.split(":", 1)[1].strip().strip("\"'")
                if value in (">", "|", ">-", "|-"):
                    # YAML folded/literal block: the text is the indented
                    # lines that follow.
                    block: list[str] = []
                    for follow in head[i + 1 :]:
                        if follow.startswith((" ", "\t")) and follow.strip():
                            block.append(follow.strip())
                        else:
                            break
                    value = " ".join(block)
                return value[:200]
        for line in head:
            stripped = line.strip().lstrip("#").strip()
            if stripped and stripped != "---" and not stripped.startswith(("name:", "tools:")):
                return stripped[:200]
        return ""
    if path.suffix == ".sh":
        for line in head[1:]:  # skip the shebang
            stripped = line.strip()
            if stripped.startswith("#") and stripped.strip("# "):
                return stripped.lstrip("# ").strip()[:200]
            if stripped and not stripped.startswith("#"):
                break
        return ""
    return ""
