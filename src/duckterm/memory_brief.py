"""Bounded continuation from independent summaries and exact retrieval pointers."""

from __future__ import annotations

from typing import Any

from duckterm import memory_assembly, memory_contract, memory_summary_input
from duckterm.core.session_api import APIError

RECENT_BYTES = 16000
MAX_RECENT_MESSAGE_CHARS = 3000


def assemble(
    key: str,
    sources: list[dict[str, Any]],
    required: str,
    revisions: list[dict[str, Any]],
    launch: dict[str, Any] | None,
    *,
    links: list[dict[str, Any]] | None = None,
    evidence_sources: list[dict[str, Any]] | None = None,
    items: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, str]]]:
    from duckterm import memory_lifecycle, memory_reconcile
    from duckterm.memory_handoff import TOOLS

    try:
        assembled = memory_assembly.assemble(
            revisions, links=links, sources=evidence_sources or sources, active_only=True
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
    leaves = [
        r
        for r in revisions
        if r.get("span") == "incremental" and r.get("fold", {}).get("tier") == 0
    ]
    original_rows, _ = memory_summary_input.records(sources)
    rows = [r for r in original_rows if r["kind"] in {"conversation", "event"}]
    eligible_count = sum(not r["reference_only"] for r in rows)
    missing = [
        r
        for r in rows
        if not r["reference_only"]
        and assembled["processed"].get(r["source"], {}).get(r["record"]) != r["hash"]
    ]
    summarized_count = eligible_count - len(missing)
    # An explicit bounded handoff can summarize current work while older
    # originals remain available through the read tools. Declare that gap;
    # never present selected coverage as complete history coverage.
    # Reconciled items, plus unmerged items from leaves not yet reconciled. The
    # handoff writer never closes an item itself.
    protected: dict[str, Any] = memory_reconcile.brief_items(
        items, leaves, set(memory_lifecycle.transitions(links or []))
    )
    # Every ref repeats a ~97-character source handle; name each handle once.
    handles: dict[str, str] = {}
    for entries in protected.values():
        for item in entries:
            short = []
            for ref in item["refs"]:
                source, version, record = ref.split(":", 2)
                handle = source + ":" + version
                name = handles.setdefault(handle, f"T{len(handles) + 1}")
                short.append(name + ":" + record)
            item["refs"] = short
    # The latest exchanges carry who did what most recently, which summaries
    # compress; measured 2026-10-30, a raw tail removed most wrong answers about
    # current state.
    recent: list[str] = []
    size = 0
    for row in reversed(original_rows):
        if row["kind"] != "conversation" or row["reference_only"]:
            continue
        if row["role"] not in {"user", "assistant"}:
            continue
        text = row["text"]
        if len(text) > MAX_RECENT_MESSAGE_CHARS:
            half = MAX_RECENT_MESSAGE_CHARS // 2
            text = text[:half] + " [... middle omitted ...] " + text[-half:]
        name = handles.setdefault(row["source"] + ":" + row["version"], f"T{len(handles) + 1}")
        line = f"[{name}:{row['record']}] {row['role']}: {text}"
        size += len(line.encode()) + 1
        if size > RECENT_BYTES:
            break
        recent.append(line)
    protected["ref_sources"] = {
        "usage": "T1:3282 is record 3282 of source handle T1: "
        "duckterm memory read <handle> --record 3282",
        **{name: handle for handle, name in handles.items()},
    }
    coverage: dict[str, Any] = {
        "declared": True,
        "unknown": False,
        "omitted": (
            [
                {
                    "reason": "pending_update",
                    "count": len(missing),
                    "sample_refs": [
                        r["source"] + ":" + r["version"] + ":" + r["record"] for r in missing[:8]
                    ],
                }
            ]
            if missing
            else []
        ),
    }
    handoff: dict[str, Any] = {
        "method": "independent_spans",
        "summary_state": "partial" if missing else "current",
        "summary_revision_id": leaves[-1]["id"] if leaves else None,
        "summary_generated_at": leaves[-1].get("created_at") if leaves else None,
        "available_records": len(rows),
        "eligible_records": eligible_count,
        "reference_only_records": len(rows) - eligible_count,
        "summarized_records": summarized_count,
        "included_records": 0,
        "omitted_records": len(rows) - summarized_count,
        "protected_kinds": {
            kind: sum(
                c["kind"] == kind for name in memory_contract.CATEGORIES for c in protected[name]
            )
            for kind in sorted(memory_contract.KIND_FIELD)
        },
        "protected_categories": {name: len(protected[name]) for name in memory_contract.CATEGORIES},
        "coverage": coverage,
    }
    # One line per item: JSON keys cost ~45 bytes an item, which at 226 items
    # pushed the oldest history out of a full brief (2026-10-31).
    lines: dict[str, Any] = {
        name: [
            f"{c['kind']} #{c['id']}: {c['text']} [{', '.join(c['refs'])}]" for c in protected[name]
        ]
        for name in memory_contract.CATEGORIES
    }
    lines["ref_sources"] = protected["ref_sources"]
    from duckterm import memory_budget

    if assembled["omitted_narratives"]:
        coverage["omitted"].append(
            {
                "reason": "budget_exhausted",
                "count": assembled["omitted_narratives"],
                "sample_refs": [],
            }
        )
    text = memory_budget.render(
        introduction=(
            f"Continue the same named DuckTerm session ({key}) " "in this new native conversation."
        ),
        launch=launch or {"session_key": key},
        required=required,
        protected=lines,
        narratives=assembled["narratives"],
        metadata=handoff,
        source_map=[
            {"kind": source["kind"], "source": source["id"] + ":" + source["version"]}
            for source in sources
            if source["kind"] == "session"
        ],
        tools=TOOLS,
        recent="\n".join(reversed(recent)),
    )
    return text, handoff, []
