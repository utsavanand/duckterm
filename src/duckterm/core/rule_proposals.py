"""Source-backed reconciliation proposals, separate from effective rules."""

import copy
import hashlib
import json
import uuid
from dataclasses import asdict
from typing import Any

from duckterm.core.agents_rules import RuleBlock, parse_rule, validate_example

EDITABLE = {"title", "text", "explanation", "when", "examples", "exceptions", "heading", "scope"}
ACTIONS = {
    "create_rule",
    "revise_rule",
    "add_example",
    "attach_evidence",
    "no_change",
    "retain_context",
    "needs_review",
    "ignore",
}
APPLICABLE = {"create_rule", "revise_rule", "add_example", "attach_evidence"}


def parse_decision(
    raw: Any, rules: list[RuleBlock], refs: set[str] | None = None
) -> dict[str, Any]:
    keys = {
        "claim",
        "memory_kind",
        "source_refs",
        "action",
        "reason",
        "target",
        "changes",
        "context",
    }
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ValueError("Invalid reconciliation decision fields")
    decision = copy.deepcopy(raw)
    action, kind = decision["action"], decision["memory_kind"]
    if (
        not isinstance(action, str)
        or action not in ACTIONS
        or not isinstance(kind, str)
        or kind not in {"procedural", "episodic", "factual", "noise", "uncertain"}
    ):
        raise ValueError("Invalid memory kind or action")
    for name in ("claim", "reason"):
        if (
            not isinstance(decision[name], str)
            or not decision[name].strip()
            or len(decision[name]) > 8000
        ):
            raise ValueError(f"Invalid proposal {name}")
    sources = decision["source_refs"]
    if (
        not isinstance(sources, list)
        or len(sources) > 100
        or any(not isinstance(s, str) or not s or len(s) > 1000 for s in sources)
    ):
        raise ValueError("Invalid source references")
    if kind != "noise" and not sources:
        raise ValueError("A decision needs original source references")
    if refs is not None and not set(sources) <= refs:
        raise ValueError("Proposal cited an unknown source")
    decision["source_refs"] = sorted(set(sources))
    target = decision["target"]
    rule = None
    if target is not None:
        if (
            not isinstance(target, dict)
            or set(target) != {"rule_id", "base_revision"}
            or type(target["base_revision"]) is not int
        ):
            raise ValueError("Invalid proposal target")
        rule = next((r for r in rules if r.id == target["rule_id"]), None)
        if rule is None or rule.revision != target["base_revision"]:
            raise ValueError("The proposal targets a missing or changed rule")
    changes = decision["changes"]
    if action in APPLICABLE | {"no_change"} and kind != "procedural":
        raise ValueError("Only procedural guidance can change rules")
    if action in {"revise_rule", "add_example", "attach_evidence"} and (
        rule is None or rule.status != "active"
    ):
        raise ValueError("Changes must target an active rule; other states need review")
    if action == "create_rule":
        if target is not None or not isinstance(changes, dict) or set(changes) != {"rule"}:
            raise ValueError("Invalid new-rule proposal")
        fields = changes["rule"]
        _fields(fields)
        if not fields.get("title") or not fields.get("text") or not fields.get("scope"):
            raise ValueError("A new rule needs title, instruction, and explicit scope")
        parse_rule({"id": "proposal", **fields})
    elif action == "revise_rule":
        if not isinstance(changes, dict) or set(changes) != {"fields"}:
            raise ValueError("Invalid rule revision")
        _fields(changes["fields"])
        assert rule is not None
        parse_rule({**asdict(rule), **changes["fields"]})
    elif action == "add_example":
        if not isinstance(changes, dict) or set(changes) != {"example"}:
            raise ValueError("Invalid example proposal")
        validate_example(changes["example"])
    elif changes is not None:
        raise ValueError("This action cannot change rule content")
    context = decision["context"]
    if action in {"retain_context", "needs_review"}:
        if action == "retain_context" and (
            target is not None or kind not in {"procedural", "episodic", "factual"}
        ):
            raise ValueError("Invalid context decision")
        if action == "needs_review" and kind not in {"procedural", "uncertain"}:
            raise ValueError("Invalid review decision")
        if context is not None:
            if (
                not isinstance(context, dict)
                or set(context) != {"destination", "text"}
                or context["destination"] not in {"summary", "project_document"}
                or not isinstance(context["text"], str)
                or not context["text"].strip()
                or len(context["text"]) > 8000
            ):
                raise ValueError("Invalid retained context")
        elif action == "retain_context":
            raise ValueError("Context content is required")
    elif context is not None:
        raise ValueError("Unexpected context")
    if action == "ignore" and (kind != "noise" or target is not None):
        raise ValueError("Invalid ignored observation")
    if kind == "uncertain" and action != "needs_review":
        raise ValueError("Uncertainty needs review")
    return decision


def _fields(fields: Any) -> None:
    if not isinstance(fields, dict) or not fields or set(fields) - EDITABLE:
        raise ValueError("Only author-editable rule fields may be proposed")


def proposal(decision: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(decision, sort_keys=True, separators=(",", ":"))
    return {"id": hashlib.sha256(payload.encode()).hexdigest(), "status": "pending", **decision}


def merge_proposals(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = copy.deepcopy(existing)
    seen = {p["id"] for p in result}
    for item in incoming:
        if item["id"] not in seen:
            result.append(item)
            seen.add(item["id"])
    return result


def review_proposals(
    rules: list[RuleBlock], submitted: list[dict[str, Any]], prior: list[dict[str, Any]]
) -> tuple[list[RuleBlock], list[dict[str, Any]]]:
    """Apply explicit dashboard decisions; preserve approved content until save."""
    output = copy.deepcopy(rules)
    known = {p["id"]: p for p in prior}
    result: list[dict[str, Any]] = []
    if len({p.get("id") for p in submitted if isinstance(p, dict)}) != len(submitted):
        raise ValueError("Duplicate proposal IDs")
    for item in submitted:
        if not isinstance(item, dict) or set(item) - {"scope_override"} != {
            "id",
            "status",
            "claim",
            "memory_kind",
            "source_refs",
            "action",
            "reason",
            "target",
            "changes",
            "context",
        }:
            raise ValueError("Invalid stored proposal")
        previous = known.get(item["id"])
        if previous and previous["status"] != "pending":
            if previous != item:
                raise ValueError("Reviewed proposals cannot be rewritten")
            result.append(previous)
            continue
        status = item["status"]
        if status not in {"pending", "accepted", "rejected"}:
            raise ValueError("Invalid review decision")
        raw = {k: v for k, v in item.items() if k not in {"id", "status", "scope_override"}}
        if previous == {**item, "status": "pending"} and status in {"pending", "rejected"}:
            result.append(item)
            continue
        decision = parse_decision(raw, output)
        if item["id"] != proposal(decision)["id"]:
            raise ValueError("Proposal content changed; reconcile it again")
        if status == "accepted":
            if item["action"] not in APPLICABLE:
                raise ValueError("This observation is not an applicable rule change")
            if "scope_override" in item:
                if decision["action"] != "create_rule":
                    raise ValueError("Only a new rule can choose scope during acceptance")
                fields = decision["changes"]["rule"]
                fields["scope"] = item["scope_override"]
                parse_rule({"id": "scope-check", **fields})
            apply_decision(output, decision)
        result.append(item)
    if not set(known) <= {p["id"] for p in result}:
        raise ValueError("Review history cannot be deleted")
    return output, copy.deepcopy(result)


def apply_decision(rules: list[RuleBlock], decision: dict[str, Any]) -> None:
    action, sources, changes = decision["action"], decision["source_refs"], decision["changes"]
    if action == "create_rule":
        fields = changes["rule"]
        normalized = " ".join(fields["text"].lower().split())
        if any(
            " ".join(r.text.lower().split()) == normalized and r.scope == fields["scope"]
            for r in rules
        ):
            raise ValueError("This instruction already exists. Update or review the existing rule.")
        rules.append(
            parse_rule(
                {
                    "id": str(uuid.uuid4()),
                    "status": "active",
                    "source": "correction",
                    "source_refs": sources,
                    "evidence": max(1, len(sources)),
                    **fields,
                }
            )
        )
        return
    target = decision["target"]
    index = next(i for i, r in enumerate(rules) if r.id == target["rule_id"])
    rule = copy.deepcopy(rules[index])
    if action == "revise_rule":
        rule = parse_rule({**asdict(rule), **changes["fields"]})
    elif action == "add_example" and changes["example"] not in rule.examples:
        rule.examples.append(changes["example"])
    rule.source_refs = sorted(set(rule.source_refs) | set(sources))
    rule.evidence = max(rule.evidence, len(rule.source_refs))
    if asdict(rule) != asdict(rules[index]):
        rule.revision += 1
    rules[index] = parse_rule(asdict(rule))
