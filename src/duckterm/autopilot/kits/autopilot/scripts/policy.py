#!/usr/bin/env python3
"""Flow exec step: decide from the spec's classification whether a person must
approve the spec before any code is written. No model decides this; the
classification is the model's, the rules are here. A classification that is
missing or unreadable requires the gate.

stdin: {"classification": {"path": ...} | null, "issue": "<issue JSON>",
"settings": "<{\"spec_on_visible\": bool} JSON>"}. Prints one JSON line."""

import json
import sys
from pathlib import Path
from typing import Any


def read_json(value: Any) -> Any:
    if isinstance(value, dict) and value.get("path"):
        try:
            return json.loads(Path(value["path"]).read_text())
        except (OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def spec_reasons(c: Any, issue: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    reasons = []
    if not isinstance(c, dict):
        return ["the spec has no readable classification"]
    security = c.get("security") or {}
    if security.get("level") != "none":
        areas = ", ".join(str(a) for a in security.get("areas") or []) or "unnamed areas"
        reasons.append(f"security touches {areas}")
    if c.get("data") != "none":
        reasons.append(f"data: {c.get('data')}")
    if c.get("ai") != "deterministic":
        reasons.append("AI behaviour")
    if (c.get("regression") or {}).get("level") == "broad":
        reasons.append("broad regression risk")
    if c.get("size") not in ("small", "medium"):
        reasons.append("large change")
    surfaces = c.get("surfaces")
    if not isinstance(surfaces, list) or len(surfaces) > 2:
        reasons.append("more than 2 surfaces")
    if settings.get("spec_on_visible") and c.get("user_visible") is not False:
        reasons.append("user visible (your setting)")
    labels = [str(lb) for lb in (issue or {}).get("labels") or []]
    if "autopilot:careful" in labels:
        reasons.append("labelled autopilot:careful")
    return reasons


def main() -> None:
    inputs = json.load(sys.stdin)
    issue = read_json(inputs.get("issue")) or {}
    settings = read_json(inputs.get("settings")) or {}
    reasons = spec_reasons(read_json(inputs.get("classification")), issue, settings)
    choice = "gate" if reasons else "skip"
    reason = "; ".join(reasons) if reasons else "routine: no rule requires a spec approval"
    print(json.dumps({"choice": choice, "reason": reason, "outputs": {}}))


if __name__ == "__main__":
    main()
