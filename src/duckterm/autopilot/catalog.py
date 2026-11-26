"""Flows that can go on autopilot, and the triggers each one declares.

A flow file names the triggers that can start it, and the input each one
fills:

    "triggers": [{"type": "github-issue", "input": "issue"}]

A flow without `triggers` runs only by hand (`duckterm kit run`), so it is not
listed here. The kits come with DuckTerm, under autopilot/kits.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from duckterm.kits import manifest

KITS = Path(__file__).parent / "kits"

# What each trigger needs connected before it can be turned on. Only the
# GitHub issue watcher exists so far; the rest are listed so a flow can
# declare them and the app can say they are coming.
TRIGGERS = {
    "github-issue": "github",
    "github-pr": "github",
    "github-ci": "github",
    "jira-issue": "jira",
    "slack-message": "slack",
    "schedule": None,
}
BUILT = frozenset({"github-issue"})


@dataclass(frozen=True)
class Flow:
    kit: str
    name: str
    description: str
    kit_path: Path
    triggers: dict[str, str]  # trigger type -> the flow input it fills
    gated: bool  # has a gate step, so a person approves something

    @property
    def id(self) -> str:
        return f"{self.kit}/{self.name}"

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kit": self.kit,
            "name": self.name,
            "description": self.description,
            "triggers": sorted(self.triggers),
            "gated": self.gated,
        }


def flows(root: Path = KITS) -> list[Flow]:
    out = []
    for path in sorted(p for p in root.iterdir() if manifest.has_manifest(p)):
        kit = manifest.load(path)
        for component in kit.by_kind("flow"):
            data = json.loads((kit.root / str(component.path)).read_text())
            triggers = _triggers(data, f"{kit.name}/{component.name}")
            if triggers:
                out.append(
                    Flow(
                        kit=kit.name,
                        name=str(data.get("name") or component.name),
                        description=str(data.get("description") or ""),
                        kit_path=kit.root,
                        triggers=triggers,
                        gated=any("gate" in step for step in data.get("steps") or []),
                    )
                )
    return out


def find(flow_id: str, root: Path = KITS) -> Flow:
    for flow in flows(root):
        if flow.id == flow_id:
            return flow
    raise KeyError(flow_id)


def _triggers(data: dict[str, object], where: str) -> dict[str, str]:
    declared = data.get("triggers") or []
    inputs = data.get("inputs") or {}
    out = {}
    for entry in declared if isinstance(declared, list) else []:
        kind = entry.get("type") if isinstance(entry, dict) else None
        if kind not in TRIGGERS:
            raise ValueError(f"{where}: unknown trigger {kind!r}")
        target = str(entry.get("input") or "")
        if target and target not in inputs:  # type: ignore[operator]
            raise ValueError(f"{where}: trigger {kind} fills undeclared input {target!r}")
        out[str(kind)] = target
    return out
