"""Versioned readers for the existing canonical context, without another store.

Reading a state is not authority to change it. Lifecycle transitions and evidence
authorization belong to the existing writer, not this structural reader.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from duckterm.memory_summary import FIELDS, MAX_SUMMARY_BYTES

# Owner taxonomy, 2026-10-30: Episodes (span summaries) plus three item
# categories. Facts are stored in the constraints field so saved revisions keep
# their six-field shape; the kind carries the category.
CATEGORIES = {
    "intent": ("goal", "task", "question"),
    "rules": ("constraint", "decision", "authority", "convention"),
    "facts": ("design", "environment", "known_issue", "risk", "learning", "reference"),
}
KIND_FIELD = {
    "goal": "goals",
    "task": "unfinished",
    "question": "questions",
    "constraint": "constraints",
    "decision": "decisions",
    "authority": "constraints",
    "convention": "constraints",
    "design": "constraints",
    "environment": "constraints",
    "known_issue": "constraints",
    "risk": "risks",
    "learning": "constraints",
    "reference": "constraints",
}
# Kinds written before the taxonomy, kept readable.
LEGACY_KINDS = {"unfinished": "task", "callout": "constraint"}
KINDS = {
    "goals": {"goal"},
    "constraints": {"constraint", "callout", "learning", "authority", "convention", "design"}
    | {"environment", "known_issue", "reference"},
    "decisions": {"decision", "learning"},
    "unfinished": {"unfinished", "task"},
    "questions": {"question"},
    "risks": {"risk"},
}


def category(kind: str) -> str:
    kind = LEGACY_KINDS.get(kind, kind)
    return next((name for name, kinds in CATEGORIES.items() if kind in kinds), "facts")


OMISSION_REASONS = {
    "oversized_record",
    "pending_update",
    "unclassified_source",
    "budget_exhausted",
    "unreadable_source",
}


class BudgetExceeded(ValueError):
    """The complete view cannot be returned; originals remain independently readable."""

    def __init__(self, value: dict[str, Any]) -> None:
        super().__init__("budget_exhausted")
        refs = sorted({ref for field in FIELDS for item in value[field] for ref in item["refs"]})
        self.kinds = sorted({claim_kind(item) for field in FIELDS for item in value[field]})
        self.coverage = {
            "declared": True,
            "unknown": False,
            "omitted": [
                {"reason": "budget_exhausted", "count": max(1, len(refs)), "sample_refs": refs[:8]}
            ],
        }


def resolve_revision(
    revision: dict[str, Any],
    lookup: Callable[[str], dict[str, Any] | None],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve claim pointers as a read view; lookup MUST enforce session/scope access.

    Returns resolved context and each v2 claim's originating revision. No original
    or referenced revision is rewritten. Missing, cyclic or conflicting references
    are failures rather than permission to return only the resolvable subset.
    """
    cache: dict[str, tuple[dict[str, Any], dict[str, str]]] = {}
    visiting: set[str] = set()

    def resolve(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        identity = row.get("id")
        if not isinstance(identity, str) or not identity:
            raise ValueError("invalid_revision_identity")
        if identity in visiting:
            raise ValueError("claim_reference_cycle")
        if identity in cache:
            return cache[identity]
        if len(visiting) >= 128:
            raise ValueError("claim_reference_depth")
        visiting.add(identity)
        version = row.get("context_version", 1)
        if type(version) is not int or version not in (1, 2):
            raise ValueError("unsupported_context_version")
        container = row.get("continuity") or row.get("memory") or {}
        if not isinstance(container, dict):
            raise ValueError("unsupported_context")
        raw = container.get("context")
        result: tuple[dict[str, Any], dict[str, str]]
        if version == 1:
            result = read_context(raw), {}
        else:
            if not isinstance(raw, dict) or set(raw) != {"overview", *FIELDS}:
                raise ValueError("unsupported_context")
            value = deepcopy(raw)
            origins: dict[str, str] = {}
            for field in FIELDS:
                if not isinstance(value[field], list):
                    raise ValueError("invalid_claims")
                for index, item in enumerate(value[field]):
                    if not isinstance(item, dict):
                        raise ValueError("invalid_claim")
                    origin = identity
                    if "ref_revision" in item:
                        if (
                            set(item) != {"id", "ref_revision"}
                            or not isinstance(item["ref_revision"], str)
                            or not item["ref_revision"]
                        ):
                            raise ValueError("invalid_claim_reference")
                        target = lookup(item["ref_revision"])
                        if not target or target.get("id") != item["ref_revision"]:
                            raise ValueError("unreadable_claim_reference")
                        context, locations = resolve(target)
                        matches = [c for c in context[field] if c.get("id") == item.get("id")]
                        if len(matches) != 1:
                            raise ValueError("unreadable_claim_reference")
                        item = value[field][index] = deepcopy(matches[0])
                        origin = locations[item["id"]]
                    if isinstance(item.get("id"), str):
                        previous = origins.setdefault(item["id"], origin)
                        if previous != origin:
                            raise ValueError("conflicting_claim_origin")
            # Folded revisions point at every child's items, so their union is not
            # bounded by the per-write cap; the brief has its own budget.
            result = read_context(value, version, check_budget=False), origins
        visiting.remove(identity)
        cache[identity] = result
        return result

    return resolve(revision)


def claim_id(field: str, text: str, refs: list[str]) -> str:
    """Contract identity; collisions require explicit writer handling, never overwrite."""
    raw = field + "\n" + text + "\n" + ",".join(sorted(refs))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def claim_kind(item: dict[str, Any]) -> str:
    """Display legacy claims without manufacturing a stored classification."""
    return str(item.get("kind", "unclassified"))


def claim_state(item: dict[str, Any]) -> str:
    return str(item.get("state", "open"))


def read_context(
    value: Any, version: int = 1, *, valid_refs: set[str] | None = None, check_budget: bool = True
) -> dict[str, Any]:
    """Keep v1 bytes/shape; callers display its claims as unclassified.

    Oversized aggregates fail with declared coverage; no subset is returned.
    When valid_refs is omitted this verifies shape only;
    it does not attest source availability or the caller's authorization.
    """
    if type(version) is not int or version not in (1, 2):
        raise ValueError("unsupported_context_version")
    if not isinstance(value, dict) or set(value) != {"overview", *FIELDS}:
        raise ValueError("unsupported_context")
    if not isinstance(value["overview"], str) or not value["overview"].strip():
        raise ValueError("invalid_narrative")
    identities: dict[str, Any] = {}
    for field in FIELDS:
        if not isinstance(value[field], list):
            raise ValueError("invalid_claims")
        for item in value[field]:
            if not isinstance(item, dict):
                raise ValueError("invalid_claim")
            keys = set(item)
            legacy = keys == {"text", "refs"}
            if version == 1:
                if not legacy:
                    raise ValueError("invalid_legacy_claim")
            elif not legacy and (
                not {"text", "refs", "kind", "id"} <= keys
                or keys
                - {
                    "text",
                    "refs",
                    "kind",
                    "id",
                    "state",
                    "supersedes",
                }
            ):
                raise ValueError("invalid_claim_fields")
            if not isinstance(item["text"], str) or not item["text"].strip():
                raise ValueError("invalid_claim")
            refs = item["refs"]
            if (
                not isinstance(refs, list)
                or not refs
                or any(not isinstance(ref, str) or not ref for ref in refs)
            ):
                raise ValueError("invalid_references")
            if valid_refs is not None and any(ref not in valid_refs for ref in refs):
                raise ValueError("unknown_reference")
            if legacy:
                continue
            if not isinstance(item["kind"], str) or item["kind"] not in KINDS[field] | {
                "unclassified"
            }:
                raise ValueError("invalid_claim_kind")
            if not isinstance(item.get("state", "open"), str) or item.get("state", "open") not in {
                "open",
                "retired",
                "superseded",
            }:
                raise ValueError("invalid_claim_state")
            identity = item["id"]
            if identity != claim_id(field, item["text"], refs):
                # Do not accept a fabricated ID or silently allocate a collision
                # suffix without an authoritative registry from the writer.
                raise ValueError("invalid_claim_identity")
            if identity in identities and identities[identity] != [field, item]:
                raise ValueError("conflicting_claim_identity")
            identities[identity] = [field, item]
            if "supersedes" in item and (
                not isinstance(item["supersedes"], str)
                or not item["supersedes"]
                or item["supersedes"] == identity
            ):
                raise ValueError("invalid_supersession")
    # Cross-revision target existence and retirement authority are writer checks.
    for identity in identities:
        seen = set()
        current: str | None = identity
        while current in identities:
            if current in seen:
                raise ValueError("supersession_cycle")
            seen.add(current)
            current = identities[current][1].get("supersedes")
    if check_budget and len(json.dumps(value, ensure_ascii=False).encode()) > MAX_SUMMARY_BYTES:
        raise BudgetExceeded(value)
    return deepcopy(value)


def read_coverage(value: Any) -> dict[str, Any]:
    """Absent coverage stays unknown. Unknown and known omissions are distinct."""
    if value is None:
        return {"declared": False, "unknown": True, "omitted": []}
    if (
        not isinstance(value, dict)
        or set(value) != {"declared", "unknown", "omitted"}
        or type(value["declared"]) is not bool
        or type(value["unknown"]) is not bool
        or not isinstance(value["omitted"], list)
    ):
        raise ValueError("invalid_coverage")
    for item in value["omitted"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"reason", "count", "sample_refs"}
            or not isinstance(item["reason"], str)
            or item["reason"] not in OMISSION_REASONS
            or type(item["count"]) is not int
            or item["count"] <= 0
            or not isinstance(item["sample_refs"], list)
            or len(item["sample_refs"]) > min(8, item["count"])
            or any(not isinstance(ref, str) or not ref for ref in item["sample_refs"])
        ):
            raise ValueError("invalid_coverage")
    return deepcopy(value)
