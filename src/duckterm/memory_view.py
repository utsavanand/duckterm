"""A read-only presentation of existing summary revisions and their visible evidence."""

from __future__ import annotations

import json
from typing import Any

from duckterm import checkpoint_clock, memory_assembly, memory_contract, memory_summary
from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import fingerprint


def page(
    values: list[Any], scope: Any, cursor: str | None, limit: int
) -> tuple[list[Any], dict[str, Any]]:
    """A continuation is valid only for the same authorized, unchanged read."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise APIError(400, "Page limit must be between 1 and 100")
    stamp = fingerprint([scope, values])
    offset = 0
    if cursor is not None:
        try:
            old_stamp, raw = cursor.split(":")
            offset = int(raw)
            if old_stamp != stamp or not 0 < offset < len(values):
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise APIError(409, "Saved memory changed; reload its first page") from exc
    selected = values[offset : offset + limit]
    end = offset + len(selected)
    return selected, {
        "snapshot": stamp,
        "total": len(values),
        "offset": offset,
        "returned": len(selected),
        "next_cursor": f"{stamp}:{end}" if end < len(values) else None,
    }


def update_metadata(update: Any) -> dict[str, Any] | None:
    if not isinstance(update, dict):
        return None
    # Corrections can quote private source/candidate text. They are not part of
    # the read receipt, especially after that evidence has been revoked.
    return {
        key: update[key]
        for key in (
            "state",
            "reason",
            "diagnostic",
            "attempted_at",
            "revision_id",
            "attempt_coverage",
            "span_coverage",
        )
        if key in update
    }


def checkpoint(
    catalog: dict[str, Any],
    sources: list[dict[str, Any]],
    identity: str,
    *,
    cursor: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Read an attempt's exact batches; never call their concatenation a new summary."""
    visible = {s["checkpoint_id"] for s in sources if s["kind"] == "checkpoint"}
    saved = next(
        (
            c
            for c in catalog["canonical"]["checkpoints"]
            if c["id"] == identity and c["id"] in visible
        ),
        None,
    )
    if saved is None:
        raise APIError(404, "Checkpoint is unavailable in this session")
    update = saved["record"].get("summary_update") or {}
    attempt = update.get("attempt_coverage")
    if not isinstance(attempt, dict):
        # Older failed attempts already persisted attempt-only coverage. A
        # successful session-wide list is not proof of which batches ran here.
        old = update.get("span_coverage") or {}
        attempt = old if old.get("scope") == "refresh_attempt" else None
    identities = list(dict.fromkeys(attempt.get("revision_ids", []))) if attempt else []
    rows, pagination = page(identities, [catalog["session"], identity, update], cursor, limit)
    batches = []
    for revision in rows:
        try:
            value = project(catalog, sources, revision)
            batches.append(
                {"revision_id": revision, "state": value["state"], "revision": value["revision"]}
            )
        except APIError as exc:
            if exc.status != 404:
                raise
            batches.append({"revision_id": revision, "state": "unavailable", "revision": None})
    return {
        "scope": "checkpoint",
        "session_key": catalog["session"],
        "checkpoint": {
            "id": identity,
            "created_at": saved["created_at"],
            "label": saved["label"],
            "summary_update": update_metadata(update),
            "attempt_coverage": attempt,
            "batches": batches,
            "pagination": pagination,
            "batch_coverage_known": attempt is not None,
            "linked_revision_id": (
                saved["record"].get("summary_ref")
                if isinstance(saved["record"].get("summary_ref"), str)
                else None
            ),
            "combined_summary": None,
            "combined_summary_reason": "independent_batch_summaries",
        },
    }


def project(
    catalog: dict[str, Any],
    sources: list[dict[str, Any]],
    requested: str | None,
    *,
    claims_cursor: str | None = None,
    claims_limit: int = 25,
    revisions_cursor: str | None = None,
    revisions_limit: int = 20,
) -> dict[str, Any]:
    if requested is not None and claims_cursor is not None:
        raise APIError(400, "Claim pagination is for current saved memory")
    revisions = [s for s in sources if s["kind"] == "revision"]
    history, history_page = page(
        [{"id": s["revision_id"], "created_at": s["created_at"]} for s in reversed(revisions)],
        [catalog["session"], "revisions"],
        revisions_cursor,
        revisions_limit,
    )
    current = catalog["current_revision"]
    identity = requested if requested is not None else current
    selected = next((s for s in revisions if s["revision_id"] == identity), None)
    if requested is not None and selected is None:
        raise APIError(404, "Summary revision is unavailable in this session")
    visible_checkpoints = {s["checkpoint_id"] for s in sources if s["kind"] == "checkpoint"}
    checkpoints = [
        cp for cp in catalog["canonical"]["checkpoints"] if cp["id"] in visible_checkpoints
    ]
    latest = checkpoints[-1] if checkpoints else None
    update = latest["record"].get("summary_update") if latest else None
    result: dict[str, Any] = {
        "scope": "revision" if requested is not None else "current",
        "session_key": catalog["session"],
        "current_revision_id": current,
        "checkpoint_clock": checkpoint_clock.project(checkpoints),
        "state": "unavailable" if identity else "empty",
        "revision": None,
        "revisions": history,
        "revisions_pagination": history_page,
        "revisions_truncated": len(history) < len(revisions),
        "latest_checkpoint": (
            {
                "id": latest["id"],
                "created_at": latest["created_at"],
                "label": latest["label"],
                "summary_update": update_metadata(update),
            }
            if latest
            else None
        ),
    }
    if selected is None:
        if claims_cursor is not None:
            raise APIError(409, "Saved memory changed; reload its first page")
        return result
    saved = next(r for r in catalog["canonical"]["revisions"] if r["id"] == selected["revision_id"])
    continuity = saved.get("continuity", {})
    records = {r["id"]: r for r in selected["records"]}
    originals = {
        s["id"] + ":" + s["version"] + ":" + r["id"]: (s, r) for s in sources for r in s["records"]
    }
    groups = []
    for field in memory_summary.FIELDS:
        claims = []
        for record in selected["records"]:
            if record["role"] != "derived_claim" or not record["id"].startswith(field + ":"):
                continue
            claim = json.loads(record["text"])
            evidence = []
            for ref in claim["refs"]:
                source, original = originals[ref]
                evidence.append(
                    {
                        "source": source["id"] + ":" + source["version"],
                        "record": original["id"],
                        "title": source["title"],
                        "kind": source["kind"],
                        "role": original["role"],
                        "retained": bool(source.get("retained")),
                    }
                )
            claims.append({"record": record["id"], "text": claim["text"], "evidence": evidence})
            claims[-1].update(
                id=claim.get("id"),
                kind=memory_contract.claim_kind(claim),
                state=memory_contract.claim_state(claim),
            )
        groups.append({"field": field, "claims": claims})
    summary = records.get("summary")
    result.update(
        state="available" if summary else "unavailable",
        revision={
            "id": saved["id"],
            "source": selected["id"] + ":" + selected["version"],
            "created_at": saved["created_at"],
            "is_current": saved["id"] == current,
            "summary": summary["text"] if summary else None,
            "overview": (continuity.get("context", {}).get("overview") if summary else None),
            "verified_at_save": continuity.get("verified") is True,
            "coverage_at_save": {
                k: continuity.get(k)
                for k in ("available_records", "summarized_records", "remaining_records", "gaps")
            },
            "groups": groups,
            "withheld_claims": len(selected["claim_refs"]) - sum(len(g["claims"]) for g in groups),
            "provenance": "derived_summary",
        },
    )
    # An exact historical revision is one span. The current Memory view also
    # assembles earlier claims; showing only the newest leaf would hide them.
    # Stop at the published revision: completed leaves of a failed later
    # checkpoint are not the published session memory.
    if requested is None and saved.get("span") == "incremental":
        rows = catalog["canonical"]["revisions"]
        rows = rows[: next(i for i, row in enumerate(rows) if row["id"] == current) + 1]
        try:
            assembled = memory_assembly.assemble(
                rows, links=catalog.get("lifecycle", []), sources=sources, display=True
            )
            by_revision = {s["revision_id"]: s for s in revisions}
            if any(
                not any(r["id"] == "summary" for r in by_revision[head["id"]]["records"])
                for head in assembled["heads"]
            ):
                raise ValueError("evidence_unavailable")
            locations: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
            for source in revisions:
                for record in source["records"]:
                    if record["role"] == "derived_claim":
                        claim = json.loads(record["text"])
                        if claim.get("id"):
                            locations.setdefault(claim["id"], (source, record))
            combined = []
            for field in memory_summary.FIELDS:
                items = []
                for claim in assembled["context"][field]:
                    origin, record = locations[claim["id"]]
                    items.append(
                        {
                            "id": claim["id"],
                            "kind": claim["kind"],
                            "state": claim.get("state", "open"),
                            "text": claim["text"],
                            "source": origin["id"] + ":" + origin["version"],
                            "record": record["id"],
                            "evidence": [
                                {
                                    "source": originals[ref][0]["id"]
                                    + ":"
                                    + originals[ref][0]["version"],
                                    "record": originals[ref][1]["id"],
                                    "title": originals[ref][0]["title"],
                                    "kind": originals[ref][0]["kind"],
                                    "role": originals[ref][1]["role"],
                                    "retained": bool(originals[ref][0].get("retained")),
                                }
                                for ref in claim["refs"]
                            ],
                        }
                    )
                combined.append({"field": field, "claims": items})
            flat = [(g["field"], claim) for g in combined for claim in g["claims"]]
            selected_claims, pagination = page(
                flat, [catalog["session"], current, "claims"], claims_cursor, claims_limit
            )
            groups = [
                {"field": field, "claims": [c for f, c in selected_claims if f == field]}
                for field in memory_summary.FIELDS
            ]
            result["memory"] = {
                "state": "available",
                "groups": groups,
                "pagination": {
                    **pagination,
                    "total_claims": pagination["total"],
                    "returned_claims": pagination["returned"],
                },
                "narratives": assembled["narratives"],
                "older_narratives_for_retrieval": assembled["omitted_narratives"],
                "span_count": sum(
                    row.get("fold", {}).get("tier") == 0 and row.get("span") == "incremental"
                    for row in rows
                ),
                "coverage": assembled["coverage"],
            }
        except (ValueError, KeyError) as exc:
            if claims_cursor is not None:
                raise APIError(409, "Saved memory changed; reload its first page") from exc
            result["memory"] = {
                "state": "unavailable",
                "groups": [],
                "narratives": [],
                "reason": (
                    "budget_exhausted"
                    if isinstance(exc, memory_contract.BudgetExceeded)
                    else "evidence_unavailable"
                ),
            }
    elif claims_cursor is not None:
        raise APIError(409, "Saved memory changed; reload its first page")
    return result
