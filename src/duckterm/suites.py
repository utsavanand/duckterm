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
      "args_choices": {"--persona": ["sport", "professional"]}   // optional
    }

- `install` is an argv template. "{dir}" is replaced with the target directory;
  a relative program path resolves against the suite's own directory; the
  process runs WITH CWD = the target directory (so a bare ["./install.sh"]
  works for installers that default to $(pwd)).
- `args_choices` maps a flag to its allowed values; the dashboard renders one
  picker per flag and appends `flag value` to the argv.
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
