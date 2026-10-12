"""Versioned readers for the existing canonical context, without another store.

Reading a state is not authority to change it. Lifecycle transitions and evidence
authorization belong to the existing writer, not this structural reader.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from duckterm.memory_summary import FIELDS

KINDS = {
    "goals": {"goal"},
    "constraints": {"constraint", "callout", "learning"},
    "decisions": {"decision", "learning"},
    "unfinished": {"unfinished"},
    "questions": {"question"},
    "risks": {"risk"},
}
OMISSION_REASONS = {
    "oversized_record",
    "pending_update",
    "unclassified_source",
    "budget_exhausted",
    "unreadable_source",
}


def claim_id(field: str, text: str, refs: list[str]) -> str:
    """Contract identity; collisions require explicit writer handling, never overwrite."""
    raw = field + "\n" + text + "\n" + ",".join(sorted(refs))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def read_context(
    value: Any, version: int = 1, *, valid_refs: set[str] | None = None
) -> dict[str, Any]:
    """Keep v1 bytes/shape; callers display its claims as unclassified.

    No size truncation applies on read: aggregate protected claims may outgrow a
    single provider budget. When valid_refs is omitted this verifies shape only;
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
            if version == 1:
                if keys != {"text", "refs"}:
                    raise ValueError("invalid_legacy_claim")
            elif not {"text", "refs", "kind", "id"} <= keys or keys - {
                "text",
                "refs",
                "kind",
                "id",
                "state",
                "supersedes",
            }:
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
            if version == 1:
                continue
            if not isinstance(item["kind"], str) or item["kind"] not in KINDS[field]:
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
