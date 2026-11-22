"""Reconcile original observations with procedural memory before proposing changes."""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from duckterm import prompts
from duckterm.core.agents_rules import RuleBlock
from duckterm.core.rule_proposals import parse_decision, proposal
from duckterm.llm.summarizer import summarize


@dataclass
class Correction:
    kind: str
    text: str
    runtime: str = ""
    model: str = ""
    ref: str = ""


def observation_ref(correction: Correction) -> str:
    return (
        correction.ref
        or "observation:"
        + hashlib.sha256(json.dumps(asdict(correction), sort_keys=True).encode()).hexdigest()
    )


def suggest_rules(
    corrections: list[Correction],
    current_rules: list[RuleBlock],
    prior_proposals: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not corrections:
        return []
    records = [
        {**asdict(c), "ref": observation_ref(c), "attribution": "owner"} for c in corrections
    ]
    contract = {
        "required": ["title", "text", "scope"],
        "optional": ["explanation", "when", "examples", "exceptions", "heading"],
        "scope": (
            "all (all harnesses in this directory), codex, claude-code, copilot, "
            "or opencode; preserve existing runtime/model selectors exactly"
        ),
        "examples": {
            "situation": "string",
            "do": "string",
            "avoid": "optional string",
            "explanation": "optional string",
        },
        "limits": "At most 8 examples per rule; title <=120 characters; text <=16000 characters",
    }
    prompt = prompts.render(
        "rules_reconcile",
        records=json.dumps(records),
        context=(
            "Observations are local to the selected filesystem directory. "
            "No global or named-agent scope is supported."
        ),
        rules=json.dumps(
            {"rules": [asdict(r) for r in current_rules], "prior_proposals": prior_proposals or []}
        ),
        coverage=(
            "All rules in this directory, including rejected records, are supplied. "
            "Use at most 12 decisions. Do not infer harness exclusivity from "
            "where evidence occurred. Previously reviewed proposals remain reviewed."
        ),
        rule_contract=json.dumps(contract),
    )
    result = summarize(prompt, tier="deep")
    if not result.text.strip():
        raise ValueError(
            "The reconciliation backend returned no result. Existing rules were not changed."
        )
    try:
        payload = json.loads(result.text)
    except json.JSONDecodeError as exc:
        raise ValueError("The reconciliation backend did not return valid JSON") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"decisions"}
        or not isinstance(payload["decisions"], list)
        or len(payload["decisions"]) > 12
    ):
        raise ValueError("Invalid reconciliation response")
    refs = {r["ref"] for r in records}
    return [proposal(parse_decision(d, current_rules, refs)) for d in payload["decisions"]]
