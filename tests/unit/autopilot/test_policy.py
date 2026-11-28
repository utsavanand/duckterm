"""The autopilot kit's spec policy: which classifications need a person to
approve the spec. The script is run as the flow runs it, JSON on stdin."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[3] / "src/duckterm/autopilot/kits/autopilot/scripts/policy.py"
ROUTINE: dict[str, Any] = {
    "surfaces": ["web"],
    "user_visible": True,
    "regression": {"level": "contained", "features": ["inbox"]},
    "security": {"level": "none", "areas": []},
    "ai": "deterministic",
    "data": "none",
    "size": "medium",
}


def _policy(tmp_path: Path, cls: Any, **inputs: Any) -> dict[str, Any]:
    f = tmp_path / "classification.json"
    f.write_text(cls if isinstance(cls, str) else json.dumps(cls))
    body = {"classification": {"path": str(f)}, "issue": "{}", **inputs}
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "spec"],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(json.loads(out.stdout.splitlines()[-1]))


def test_a_routine_fix_skips_the_spec_gate(tmp_path: Path) -> None:
    assert _policy(tmp_path, ROUTINE)["choice"] == "skip"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"security": {"level": "touches", "areas": ["auth"]}}, "security touches auth"),
        ({"data": "schema"}, "data: schema"),
        ({"ai": "model_dependent"}, "AI behaviour"),
        ({"regression": {"level": "broad", "features": []}}, "broad regression risk"),
        ({"size": "large"}, "large change"),
        ({"surfaces": ["web", "server", "mac"]}, "more than 2 surfaces"),
        # a value the policy does not know is never read as routine
        ({"security": {"level": "minor"}}, "security touches unnamed areas"),
        ({"size": "tiny"}, "large change"),
    ],
)
def test_each_risk_requires_the_spec_gate(
    tmp_path: Path, change: dict[str, Any], reason: str
) -> None:
    decided = _policy(tmp_path, {**ROUTINE, **change})
    assert (decided["choice"], decided["reason"]) == ("gate", reason)


def test_an_unreadable_classification_requires_the_gate(tmp_path: Path) -> None:
    decided = _policy(tmp_path, "this is routine, trust me")
    assert (decided["choice"], decided["reason"]) == (
        "gate",
        "the spec has no readable classification",
    )


@pytest.mark.parametrize(
    ("inputs", "reason"),
    [
        ({"settings": json.dumps({"spec_on_visible": True})}, "user visible (your setting)"),
        ({"issue": json.dumps({"labels": ["autopilot:careful"]})}, "labelled autopilot:careful"),
    ],
)
def test_the_person_can_require_the_gate_for_more(
    tmp_path: Path, inputs: dict[str, Any], reason: str
) -> None:
    decided = _policy(tmp_path, ROUTINE, **inputs)
    assert (decided["choice"], decided["reason"]) == ("gate", reason)
