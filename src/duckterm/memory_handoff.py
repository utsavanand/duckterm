"""Bounded onboarding over maintained records. No provider or persistence calls."""

from __future__ import annotations

import json
from typing import Any

from duckterm import prompts
from duckterm.memory_budget import MAX_BRIEF_BYTES as MAX_BRIEF_BYTES
from duckterm.persistence.saved_state import fingerprint

MAX_REQUIRED_BYTES = 16000
ORIGINAL_KINDS = {"conversation", "session", "task", "inbox", "event", "artifact"}

TOOLS = prompts.load("handoff_tools")


def records(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for source in sources:
        if source["kind"] not in ORIGINAL_KINDS:
            continue
        for position, row in enumerate(source["records"]):
            if row["role"] == "derived_context":
                continue
            result.append(
                {
                    "source": source["id"],
                    "version": source["version"],
                    "kind": source["kind"],
                    "current": bool(source.get("current")),
                    "record": row["id"],
                    "position": position,
                    "role": row["role"],
                    "text": row["text"],
                    "hash": fingerprint([row["id"], row["role"], row["text"]]),
                }
            )
    return result


def coverage(
    rows: list[dict[str, Any]], prior: dict[str, Any] | None
) -> tuple[dict[str, dict[str, str]], int]:
    """Only exact recorded coverage counts; a recent legacy overview is not coverage."""
    processed = (prior or {}).get("continuity", {}).get("processed", {})
    valid: dict[str, dict[str, str]] = {}
    count = 0
    for row in rows:
        if processed.get(row["source"], {}).get(row["record"]) == row["hash"]:
            valid.setdefault(row["source"], {})[row["record"]] = row["hash"]
            count += 1
    return valid, count


def usable_prior(
    sources: list[dict[str, Any]], required: str, prior: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Do not deliver derived text from revoked scope or rewritten/deleted evidence."""
    if not prior:
        return None
    if prior.get("source", {}).get("scope_hash") != fingerprint(json.loads(required)["scope"]):
        return None
    continuity = prior.get("continuity")
    if continuity:
        processed, _ = coverage(records(sources), prior)
        if not continuity.get("verified") or processed != continuity.get("processed", {}):
            return None
    return prior


def assemble(
    key: str,
    sources: list[dict[str, Any]],
    required: str,
    prior: dict[str, Any] | None,
    *,
    launch: dict[str, Any] | None = None,
    revisions: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    evidence_sources: list[dict[str, Any]] | None = None,
    items: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, str]]]:
    """Include required records intact; add complete recent records within the launch budget."""
    if revisions is not None and any(r.get("span") == "incremental" for r in revisions):
        from duckterm import memory_brief

        return memory_brief.assemble(
            key,
            sources,
            required,
            revisions,
            launch,
            links=links,
            evidence_sources=evidence_sources,
            items=items,
        )
    from duckterm import memory_budget, memory_summary

    prior = usable_prior(sources, required, prior)
    rows = records(sources)
    _, count = coverage(rows, prior)
    continuity = (prior or {}).get("continuity", {})
    context = continuity.get("context") or {}
    protected = {
        field: [c for c in context.get(field, []) if c.get("state", "open") == "open"]
        for field in memory_summary.FIELDS
    }
    if not prior:
        state = "unavailable"
    elif not continuity:
        state = "legacy"
    elif count == len(rows) and continuity.get("verified"):
        state = "current"
    else:
        state = "partial"
    handoff = {
        "method": "maintained",
        "summary_revision_id": (prior or {}).get("id"),
        "summary_generated_at": (prior or {}).get("created_at"),
        "summary_state": state,
        "available_records": len(rows),
        "summarized_records": count,
        "included_records": 0,
        "omitted_records": len(rows) - count,
        "coverage": (prior or {}).get("coverage")
        or {"declared": False, "unknown": True, "omitted": []},
    }
    narrative = context.get("overview") or (prior or {}).get("summary")
    text = memory_budget.render(
        introduction=(
            f"Continue the same named DuckTerm session ({key}) " "in this new native conversation."
        ),
        launch=launch or {"session_key": key},
        required=required,
        protected=protected,
        narratives=(
            [
                {
                    "revision": (prior or {}).get("id"),
                    "saved_at": (prior or {}).get("created_at"),
                    "text": narrative,
                }
            ]
            if narrative
            else []
        ),
        metadata=handoff,
        source_map=[
            {"kind": source["kind"], "source": source["id"] + ":" + source["version"]}
            for source in sources
        ],
        tools=TOOLS,
    )
    return text, handoff, []
