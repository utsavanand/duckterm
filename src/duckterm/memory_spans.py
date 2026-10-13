"""Independent conversation spans for the shared summary writer.

Boundaries come from captured originals, never by subtracting summaries. The reader
assembles previous leaves separately; generation never receives an earlier summary.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from duckterm import memory_summary_input, prompts
from duckterm.persistence.saved_state import fingerprint

POLICY = "independent-conversation-spans-v2"
MAX_INPUT_BYTES = 48000
# The 48 KB call budget reserves 32 KB for generation. Bound the *encoded
# prompt*, including metadata/instructions, to the remaining 16 KB.
GENERATION_RESERVE_BYTES = 32000
MAX_GENERATION_BYTES = MAX_INPUT_BYTES - GENERATION_RESERVE_BYTES
MAX_RECORDS = 50


def original_rows(captured: dict[str, Any]) -> list[dict[str, Any]]:
    rows, _ = memory_summary_input.records(captured["memory_sources"])
    return [row for row in rows if row["kind"] in {"conversation", "event"}]


def boundary(captured: dict[str, Any], *, empty: bool = False) -> dict[str, Any]:
    """An explicit observed boundary, or an explicitly requested source origin."""
    rows = original_rows(captured)
    return _boundary(captured, rows, {} if empty else _counts(rows))


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        result[row["source"]] = row["position"] + 1
    return result


def _boundary(
    captured: dict[str, Any], rows: list[dict[str, Any]], counts: dict[str, int]
) -> dict[str, Any]:
    prefixes: dict[str, list[Any]] = {}
    versions: dict[str, str] = {}
    for row in rows:
        source = row["source"]
        versions[source] = row["version"]
        if row["position"] < counts.get(source, 0):
            prefixes.setdefault(source, []).append([row["record"], row["hash"]])
    return {
        "policy": POLICY,
        "scope": captured["source"]["scope_hash"],
        "sources": {
            source: {
                "count": count,
                "version": versions[source],
                "prefix_hash": fingerprint(prefixes.get(source, [])),
            }
            for source, count in counts.items()
            if count
        },
    }


def _lower_counts(
    captured: dict[str, Any], rows: list[dict[str, Any]], lower: dict[str, Any]
) -> dict[str, int]:
    if (
        set(lower) != {"policy", "scope", "sources"}
        or lower["policy"] != POLICY
        or lower["scope"] != captured["source"]["scope_hash"]
        or not isinstance(lower["sources"], dict)
    ):
        raise ValueError("explicit_span_boundary_required")
    counts = {}
    available = _counts(rows)
    for source, value in lower["sources"].items():
        if (
            not isinstance(value, dict)
            or set(value) != {"count", "version", "prefix_hash"}
            or type(value["count"]) is not int
            or value["count"] <= 0
            or not isinstance(value["version"], str)
            or not isinstance(value["prefix_hash"], str)
        ):
            raise ValueError("invalid_span_boundary")
        count = value["count"]
        if available.get(source, 0) < count:
            raise ValueError("span_source_removed")
        counts[source] = count
    current = _boundary(captured, rows, counts)
    if any(
        current["sources"][source]["prefix_hash"] != value["prefix_hash"]
        for source, value in lower["sources"].items()
    ):
        raise ValueError("span_source_rewritten")
    return counts


def prompt(plan: dict[str, Any]) -> str:
    """The entire generation prompt, including metadata, fits the byte budget."""
    records = [
        {
            **{k: row[k] for k in ("source", "version", "record", "position", "role", "text")},
            "ref": row["source"] + ":" + row["version"] + ":" + row["record"],
        }
        for row in plan["selected"]
    ]
    return prompts.render(
        "span_write", payload=json.dumps({"records": records}, ensure_ascii=False)
    )


def plan(captured: dict[str, Any], lower: dict[str, Any]) -> dict[str, Any]:
    """Select one contiguous, byte-bounded subrange of a frozen capture.

    The caller keeps the capture fixed across subranges. On failure it retries
    from the preceding successful boundary. No cumulative revision is accepted
    as a boundary and no history is silently backfilled on a policy change.
    """
    rows = original_rows(captured)
    counts = _lower_counts(captured, rows, lower)
    next_counts = dict(counts)
    result: dict[str, Any] = {
        "rows": rows,
        "selected": [],
        "prior_context": None,
        "context_version": 2,
        "span": "incremental",
        "processed": {},
        "invalidated": False,
        "retained_sources": [],
        "memory_sources": [],
        "required_work": {"kinds": {}},
    }
    pending = []
    consumed = []
    selection = captured.get("checkpoint_selection")
    covered = captured.get("span_covered", {})
    stopped = False
    for row in rows:
        if row["position"] < counts.get(row["source"], 0):
            continue
        identity = (row["source"], row["record"])
        if (selection is not None and identity not in selection) or covered.get(identity) == row[
            "hash"
        ]:
            if not stopped:
                next_counts[row["source"]] = row["position"] + 1
            continue
        if stopped:
            if not row["reference_only"]:
                pending.append(row)
            continue
        if not row["reference_only"]:
            candidate = {**result, "selected": [*result["selected"], row]}
            if (
                len(candidate["selected"])
                > captured.get("checkpoint_settings", {}).get("batch_records", MAX_RECORDS)
                or len(prompt(candidate).encode()) > MAX_GENERATION_BYTES
            ):
                stopped = True
                pending.append(row)
                continue
            result["selected"].append(row)
        consumed.append(row)
        next_counts[row["source"]] = row["position"] + 1
    result.update(
        lower=deepcopy(lower),
        upper=_boundary(captured, rows, _counts(rows)),
        next_boundary=_boundary(captured, rows, next_counts),
        pending_records=len(pending),
        pending_refs=[r["source"] + ":" + r["version"] + ":" + r["record"] for r in pending[:8]],
        oversized=bool(pending and not result["selected"]),
    )
    result["ranges"] = [
        {
            "source": source,
            "version": value["version"],
            "first": counts.get(source, 0),
            "end": value["count"],
        }
        for source, value in result["next_boundary"]["sources"].items()
        if value["count"] > counts.get(source, 0)
    ]
    if selection is not None or covered:
        # A skipped window is not processed coverage. Record only consumed ranges,
        # so a larger later checkpoint can fill holes without overlapping leaves.
        result["ranges"] = []
        for row in consumed:
            ranges = result["ranges"]
            if (
                ranges
                and ranges[-1]["source"] == row["source"]
                and ranges[-1]["end"] == row["position"]
            ):
                ranges[-1]["end"] += 1
            else:
                ranges.append(
                    {
                        "source": row["source"],
                        "version": row["version"],
                        "first": row["position"],
                        "end": row["position"] + 1,
                    }
                )
    result["input_key"] = "span:" + fingerprint(
        {
            "policy": POLICY,
            "summary_policy": captured["policy"],
            "writer_instructions": fingerprint(prompts.load("span_write")),
            "checkpoint_models": captured.get("checkpoint_settings", {}),
            "scope": lower["scope"],
            "ranges": result["ranges"],
            "records": [
                [r["source"], r["version"], r["record"], r["hash"]] for r in result["selected"]
            ],
        }
    )
    return result


def continuity(plan: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Coverage belongs to this leaf, never the union of previous summaries."""
    processed: dict[str, dict[str, str]] = {}
    for row in plan["selected"]:
        processed.setdefault(row["source"], {})[row["record"]] = row["hash"]
    return {
        "verified": True,
        "context": context,
        "processed": processed,
        "update_complete": True,
        "available_records": len(plan["selected"]),
        "summarized_records": len(plan["selected"]),
        "remaining_records": 0,
        "pending_updates": {},
        "gaps": [],
    }


def metadata(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "context_version": 2,
        "span": "incremental",
        "fold": {"tier": 0, "children": []},
        "coverage": {"declared": True, "unknown": False, "omitted": []},
        "span_input": {
            "policy": POLICY,
            "lower": plan["lower"],
            "upper": plan["upper"],
            "next_boundary": plan["next_boundary"],
            "ranges": plan["ranges"],
            "pending_records": plan["pending_records"],
            "pending_refs": plan["pending_refs"],
        },
    }


def overlaps(plan: dict[str, Any], revision: dict[str, Any]) -> bool:
    """Reject a second leaf for already committed original positions.

    Appending to a source changes its version, but does not make the earlier
    positions new work. Exact batch retries are handled before this check.
    """
    if revision.get("span") != "incremental" or revision.get("fold", {}).get("tier") != 0:
        return False
    previous = revision.get("span_input", {}).get("ranges", [])
    return any(
        a["source"] == b["source"] and max(a["first"], b["first"]) < min(a["end"], b["end"])
        for a in plan["ranges"]
        for b in previous
    )
