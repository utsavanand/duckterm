#!/usr/bin/env python3
"""Decide where a person must approve. No model decides this; the
classifications are models', the rules are here. Anything missing or
unreadable requires the gate, and the policy can only add gates.

`policy.py spec` (a flow exec step): from the spec's classification, whether
the spec needs approval before any code is written. stdin: {"classification":
{"path": ...} | null, "issue": "<issue JSON>", "settings": "<{\"spec_on_visible\":
bool} JSON>"}.

`policy.py merge` (run by the DuckTerm server once CI passed): from the spec's
classification, the security review's classification of the diff and the
autopilot's path rules, whether the merge needs approval. stdin:
{"spec_class": {...} | path, "diff_class": {...} | path, "path_hits":
[{"field", "path"}], "spec_gated": bool}.

Prints one JSON line: {"choice": "gate" | "skip" | "auto", "reason", "outputs":
{"reasons": [...]}}."""

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


REGRESSION = ("none", "contained", "broad")
SIZE = ("small", "medium", "large")


def _rank(scale: tuple[str, ...], value: Any) -> int:
    # an unknown value ranks above every known one
    return scale.index(value) if value in scale else len(scale)


def higher_fields(spec: dict[str, Any], diff: dict[str, Any]) -> list[str]:
    """Fields the reviewer, reading the diff, rated higher than the spec did."""
    out = []
    if _rank(REGRESSION, (diff.get("regression") or {}).get("level")) > _rank(
        REGRESSION, (spec.get("regression") or {}).get("level")
    ):
        out.append("regression")
    if (diff.get("security") or {}).get("level") != "none" and (spec.get("security") or {}).get(
        "level"
    ) == "none":
        out.append("security")
    for field, quiet in (("ai", "deterministic"), ("data", "none")):
        if diff.get(field) != quiet and spec.get(field) == quiet:
            out.append(field)
    if diff.get("user_visible") is not False and spec.get("user_visible") is False:
        out.append("user visible")
    if _rank(SIZE, diff.get("size")) > _rank(SIZE, spec.get("size")):
        out.append("size")
    if not set(diff.get("surfaces") or []) <= set(spec.get("surfaces") or []):
        out.append("surfaces")
    return out


def merge_reasons(
    spec: Any, diff: Any, path_hits: list[dict[str, str]], spec_gated: bool
) -> list[str]:
    reasons = []
    if spec_gated:
        reasons.append("the spec gate was required")
    if not isinstance(diff, dict):
        reasons.append("the security review gave no readable classification")
        diff = {}
    spec = spec if isinstance(spec, dict) else {}
    for c in (spec, diff):
        regression = c.get("regression") or {}
        if regression.get("level") == "contained":
            features = ", ".join(str(f) for f in regression.get("features") or []) or "a feature"
            reasons.append(f"could break {features}")
            break
    if spec.get("user_visible") is not False or diff.get("user_visible") is True:
        reasons.append("user visible")
    for hit in path_hits:
        reasons.append(f"path rule ({hit['field']}): {hit['path']}")
    higher = higher_fields(spec, diff) if diff else []
    if higher:
        reasons.append("the review rated it higher than the spec: " + ", ".join(higher))
    return list(dict.fromkeys(reasons))


def main() -> None:
    inputs = json.load(sys.stdin)
    if sys.argv[1:] == ["merge"]:
        reasons = merge_reasons(
            read_json(inputs.get("spec_class")),
            read_json(inputs.get("diff_class")),
            list(inputs.get("path_hits") or []),
            bool(inputs.get("spec_gated")),
        )
        choice = "gate" if reasons else "auto"
        reason = "; ".join(reasons) if reasons else "routine: no rule requires a merge approval"
    else:
        issue = read_json(inputs.get("issue")) or {}
        settings = read_json(inputs.get("settings")) or {}
        reasons = spec_reasons(read_json(inputs.get("classification")), issue, settings)
        choice = "gate" if reasons else "skip"
        reason = "; ".join(reasons) if reasons else "routine: no rule requires a spec approval"
    print(json.dumps({"choice": choice, "reason": reason, "outputs": {"reasons": reasons}}))


if __name__ == "__main__":
    main()
