"""A read-only presentation of existing summary revisions and their visible evidence."""

from __future__ import annotations

import json
from typing import Any

from duckterm import memory_summary
from duckterm.core.session_api import APIError


def project(
    catalog: dict[str, Any], sources: list[dict[str, Any]], requested: str | None
) -> dict[str, Any]:
    revisions = [s for s in sources if s["kind"] == "revision"]
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
        "session_key": catalog["session"],
        "current_revision_id": current,
        "state": "unavailable" if identity else "empty",
        "revision": None,
        "revisions": [
            {"id": s["revision_id"], "created_at": s["created_at"]}
            for s in reversed(revisions[-20:])
        ],
        "revisions_truncated": len(revisions) > 20,
        "latest_checkpoint": (
            {
                "id": latest["id"],
                "created_at": latest["created_at"],
                "label": latest["label"],
                "summary_update": (
                    {
                        k: update[k]
                        for k in ("state", "reason", "diagnostic", "attempted_at", "revision_id")
                        if k in update
                    }
                    if isinstance(update, dict)
                    else None
                ),
            }
            if latest
            else None
        ),
    }
    if selected is None:
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
    return result
