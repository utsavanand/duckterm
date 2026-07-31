"""Installable harnesses: suites of skills, hooks, sub-agents, and guardrails
that get installed into a project (or globally) for the agents Duckterm runs —
uv-suite is the canonical example. Distinct from the runtime ADAPTERS in
harnesses.py, which teach Duckterm how to drive/observe one agent CLI.

The contract is a `duckterm-harness.json` at the suite's root:

    {
      "name": "uv-suite",
      "description": "Agents, skills, hooks, and guardrails for Claude Code",
      "install": ["./install.sh", "--project", "{dir}"],
      "uninstall": ["./uninstall.sh"],                    // optional
      "args_choices": {"--persona": ["sport", "professional"]},  // optional
      "detect": ".claude/skills/uv-help",                 // optional
      "compatible": ["claude-code"]                       // optional
    }

- `install` is an argv template. "{dir}" is replaced with the target directory;
  a relative program path resolves against the suite's own directory; the
  process runs WITH CWD = the target directory (so a bare ["./install.sh"]
  works for installers that default to $(pwd)).
- `args_choices` maps a flag to its allowed values; the dashboard renders one
  picker per flag and appends `flag value` to the argv.
- `detect` is a path (relative to an install target) whose existence proves
  the suite is installed there — how the dashboard shows which meta-harness a
  session is running under.
- `compatible` names the agent harnesses the suite works with (registry names
  like "claude-code"); omitted means it doesn't say.
- Fallback: a directory with an `install.sh` but no manifest is accepted as
  {name: <dirname>, install: ["./install.sh"]} — that's enough for most
  one-script installers; options ride in as extra args.

Installers are local scripts the user registered by path themselves and run as
the same user — the same trust as launching an agent in a terminal.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

MANIFEST = "duckterm-harness.json"


@dataclass
class Suite:
    name: str
    description: str
    path: Path
    install: list[str]
    uninstall: list[str] | None
    args_choices: dict[str, list[str]]
    detect: str | None
    compatible: list[str]
    has_manifest: bool


def load(path: Path) -> Suite:
    """Read a suite from its directory. Raises ValueError if the directory has
    neither a manifest nor an install.sh."""
    manifest = path / MANIFEST
    if manifest.is_file():
        data = json.loads(manifest.read_text())
        name = str(data.get("name") or path.name)
        install = data.get("install")
        if not isinstance(install, list) or not install:
            raise ValueError(f"{MANIFEST} needs a non-empty `install` argv list")
        uninstall = data.get("uninstall")
        raw_choices = data.get("args_choices")
        choices = (
            {
                str(flag): [str(v) for v in values]
                for flag, values in raw_choices.items()
                if isinstance(values, list) and values
            }
            if isinstance(raw_choices, dict)
            else {}
        )
        return Suite(
            name=name,
            description=str(data.get("description") or ""),
            path=path,
            install=[str(a) for a in install],
            uninstall=[str(a) for a in uninstall] if isinstance(uninstall, list) else None,
            args_choices=choices,
            detect=str(data["detect"]) if data.get("detect") else None,
            compatible=[str(c) for c in data.get("compatible") or []],
            has_manifest=True,
        )
    if (path / "install.sh").is_file():
        return Suite(
            name=path.name,
            description="install.sh (no manifest)",
            path=path,
            install=["./install.sh"],
            uninstall=None,
            args_choices={},
            detect=None,
            compatible=[],
            has_manifest=False,
        )
    raise ValueError(f"{path} has neither {MANIFEST} nor install.sh")


def run_install(suite: Suite, target: Path, extra_args: list[str]) -> tuple[bool, str]:
    """Run the suite's installer against `target`. Returns (ok, output)."""
    return _run(suite, suite.install, target, extra_args)


def run_uninstall(suite: Suite, target: Path, extra_args: list[str]) -> tuple[bool, str]:
    """Run the suite's uninstaller against `target` (caller checks it exists)."""
    assert suite.uninstall is not None
    return _run(suite, suite.uninstall, target, extra_args)


def _run(suite: Suite, base: list[str], target: Path, extra_args: list[str]) -> tuple[bool, str]:
    argv = [a.replace("{dir}", str(target)) for a in base] + list(extra_args)
    # A relative program path is relative to the SUITE (that's where the script
    # lives); cwd is the TARGET (that's what one-script installers act on).
    program = Path(argv[0])
    if not program.is_absolute():
        argv[0] = str((suite.path / program).resolve())
    try:
        result = subprocess.run(
            argv,
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


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
