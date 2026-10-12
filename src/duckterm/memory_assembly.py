"""Read independent summary leaves and folds without generating another summary.

The caller supplies only revisions authorized for this session and sharing scope.
Legacy summaries remain separately identifiable; they never become span boundaries.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from duckterm import memory_contract, memory_summary
from duckterm.core import memory_folding


def assemble(
    revisions: list[dict[str, Any]],
    *,
    links: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    active_only: bool = False,
) -> dict[str, Any]:
    """Resolve all active protected claims; use recent heads for narrative only.

    Folding replaces narrative heads, not evidence or open claims. Unknown,
    incomplete and unresolvable inputs fail rather than produce a partial union.
    """
    index = {row["id"]: row for row in revisions}
    heads = memory_folding.active(revisions)
    context: dict[str, Any] = {field: [] for field in memory_summary.FIELDS}
    seen: dict[str, tuple[str, dict[str, Any]]] = {}
    narratives = []
    processed: dict[str, dict[str, str]] = {}
    for row in revisions:
        if row.get("span") != "incremental" or row.get("fold", {}).get("tier") != 0:
            continue
        for source, records in row.get("continuity", {}).get("processed", {}).items():
            for record, digest in records.items():
                previous = processed.setdefault(source, {}).setdefault(record, digest)
                if previous != digest:
                    raise ValueError("conflicting_span_evidence")
    for head in heads:
        row = index[head["id"]]
        coverage = memory_contract.read_coverage(row.get("coverage"))
        if (
            row.get("summary_validation", {}).get("ready") is not True
            or not coverage["declared"]
            or coverage["unknown"]
            or coverage["omitted"]
        ):
            raise ValueError("incomplete_span_input")
        value, _ = memory_contract.resolve_revision(row, index.get)
        narratives.append({"revision": row["id"], "text": value["overview"]})
        for field in memory_summary.FIELDS:
            for claim in value[field]:
                identity = claim.get("id")
                if not identity:
                    raise ValueError("unclassified_span_claim")
                existing = seen.setdefault(identity, (field, claim))
                if existing != (field, claim):
                    raise ValueError("conflicting_claim_identity")
                if not any(item["id"] == identity for item in context[field]):
                    context[field].append(deepcopy(claim))
    # The structural reader checks cross-head cycles and aggregate size. Do not
    # silently slice a protected union to make a handoff look complete.
    context["overview"] = narratives[-1]["text"] if narratives else "No span summary available."
    if links:
        from duckterm import memory_lifecycle

        context = memory_lifecycle.project(context, links, sources or [])
    if active_only:
        # The handoff budget protects open work. Explicitly closed claims stay
        # in immutable revisions and retrieval, not in the active-claim budget.
        context = {
            **context,
            **{
                field: [c for c in context[field] if c.get("state", "open") == "open"]
                for field in memory_summary.FIELDS
            },
        }
    refs = (
        {s["id"] + ":" + s["version"] + ":" + r["id"] for s in sources for r in s["records"]}
        if sources is not None
        else None
    )
    context = memory_contract.read_context(context, 2, valid_refs=refs)
    return {
        "context": context,
        "context_version": 2,
        "heads": heads,
        "narratives": narratives[-10:],
        "omitted_narratives": max(0, len(narratives) - 10),
        "processed": processed,
        "coverage": {"declared": True, "unknown": False, "omitted": []},
        "legacy_revisions": [r["id"] for r in revisions if r.get("span") != "incremental"],
    }
