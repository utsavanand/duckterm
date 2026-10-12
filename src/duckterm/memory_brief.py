"""Bounded continuation from independent summaries and exact retrieval pointers."""

from __future__ import annotations

from typing import Any

from duckterm import memory_assembly, memory_contract, memory_summary, memory_summary_input
from duckterm.core.session_api import APIError


def assemble(
    key: str,
    sources: list[dict[str, Any]],
    required: str,
    revisions: list[dict[str, Any]],
    launch: dict[str, Any] | None,
    *,
    links: list[dict[str, Any]] | None = None,
    evidence_sources: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, str]]]:
    from duckterm.memory_handoff import TOOLS

    try:
        assembled = memory_assembly.assemble(
            revisions, links=links, sources=evidence_sources or sources
        )
    except memory_contract.BudgetExceeded as exc:
        raise APIError(
            409,
            "Open memory claims exceed the handoff budget ("
            + ", ".join(exc.kinds)
            + "); source agent kept",
        ) from exc
    except ValueError as exc:
        raise APIError(409, "Saved span memory cannot be resolved; source agent kept") from exc
    original_rows, _ = memory_summary_input.records(sources)
    rows = [r for r in original_rows if r["kind"] in {"conversation", "event"}]
    eligible_count = sum(not r["reference_only"] for r in rows)
    missing = [
        r
        for r in rows
        if not r["reference_only"]
        and assembled["processed"].get(r["source"], {}).get(r["record"]) != r["hash"]
    ]
    if missing:
        raise APIError(
            409, f"{len(missing)} conversation records still need a summary; source agent kept"
        )
    context = assembled["context"]
    # Lifecycle changes must be explicit persisted claims, never an inference by
    # the handoff writer. No narrative provider gets to retire protected claims.
    protected = {
        field: [c for c in context[field] if c.get("state", "open") == "open"]
        for field in memory_summary.FIELDS
    }
    coverage: dict[str, Any] = {
        "declared": True,
        "unknown": False,
        "omitted": [],
    }
    leaves = [
        r
        for r in revisions
        if r.get("span") == "incremental" and r.get("fold", {}).get("tier") == 0
    ]
    handoff: dict[str, Any] = {
        "method": "independent_spans",
        "summary_state": "current",
        "summary_revision_id": leaves[-1]["id"] if leaves else None,
        "summary_generated_at": leaves[-1].get("created_at") if leaves else None,
        "available_records": len(rows),
        "eligible_records": eligible_count,
        "reference_only_records": len(rows) - eligible_count,
        "summarized_records": eligible_count,
        "included_records": 0,
        "omitted_records": len(rows) - eligible_count,
        "protected_kinds": {field: len(items) for field, items in protected.items()},
        "coverage": coverage,
    }
    from duckterm import memory_budget

    if assembled["omitted_narratives"]:
        coverage["omitted"] = [
            {
                "reason": "budget_exhausted",
                "count": assembled["omitted_narratives"],
                "sample_refs": [],
            }
        ]
    text = memory_budget.render(
        introduction=(
            f"Continue the same named DuckTerm session ({key}) " "in this new native conversation."
        ),
        launch=launch or {"session_key": key},
        required=required,
        protected=protected,
        narratives=assembled["narratives"],
        metadata=handoff,
        source_map=[
            {"kind": source["kind"], "source": source["id"] + ":" + source["version"]}
            for source in sources
            if source["kind"] == "session"
        ],
        tools=TOOLS,
    )
    return text, handoff, []
