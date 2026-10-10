"""Read projections and retained versions from canonical memory references only."""

from __future__ import annotations

import json
from typing import Any

from duckterm import memory_sources, memory_summary, memory_versions
from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import REVISION_BUCKET, fingerprint

LINK_BUCKET = "memory_link_v1"


def canonical(server: Any, key: str, grant: dict[str, Any]) -> dict[str, Any]:
    conn = server.history._conn
    revisions, links, checkpoints = [], [], []
    for row in conn.execute(
        "SELECT id,bucket,text FROM digest_items WHERE session_key=? AND bucket IN (?,?) "
        "ORDER BY created_at,rowid",
        (key, REVISION_BUCKET, LINK_BUCKET),
    ):
        if row["bucket"] == REVISION_BUCKET:
            value = server.digests.revision(key, row["id"])
            if value and value["source"].get("scope_hash") == fingerprint(grant):
                revisions.append(value)
        else:
            try:
                value = json.loads(row["text"])
                if value.get("root") == grant.get("root", ""):
                    links.append(value)
            except (ValueError, AttributeError):
                continue
    for row in conn.execute(
        "SELECT id,label,record_json,created_at FROM checkpoints WHERE session_key=? "
        "ORDER BY created_at,rowid",
        (key,),
    ):
        try:
            record = json.loads(row["record_json"])
            if isinstance(record, dict):
                checkpoints.append({**dict(row), "record": record})
        except ValueError:
            continue
    return {"revisions": revisions, "links": links, "checkpoints": checkpoints}


def references(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    result.extend(canonical["revisions"])
    result.extend(canonical["links"])
    result.extend(cp["record"] for cp in canonical["checkpoints"])
    return result


def retained(
    catalog: dict[str, Any], sources: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Only read registered snapshots whose source is still present and authorized."""
    found = {s["id"] + ":" + s["version"]: s for s in sources}
    allowed = {s["id"] for s in catalog["sources"]}
    unavailable = []
    for handle, ref in {**catalog["retained"], **catalog["text_retained"]}.items():
        if handle in found or ref.get("unavailable") or ref["id"] not in allowed:
            continue
        try:
            value = (
                memory_sources.read_native(catalog["session"], ref)
                if ref.get("kind") == "conversation"
                else memory_versions.read(catalog["session"], ref, catalog["root"])
            )
            if value["id"] + ":" + value["version"] != handle:
                raise APIError(409, "Retained source identity changed")
            found[handle] = {**value, "retained": True, "current": False}
        except (APIError, OSError, UnicodeError) as exc:
            unavailable.append({"id": handle, "reason": str(exc)})
    return list(found.values()), unavailable


def projection(
    key: str, kind: str, identity: str, title: str, records: list[dict[str, Any]], **meta: Any
) -> dict[str, Any]:
    return {
        "id": fingerprint([key, kind, identity])[:32],
        "kind": kind,
        "title": title,
        "version": fingerprint(records),
        "records": records,
        **meta,
    }


def project(catalog: dict[str, Any], originals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summary text stays derived; a missing/revoked citation never exposes cached claims."""
    available = {
        s["id"] + ":" + s["version"] + ":" + r["id"] for s in originals for r in s["records"]
    }
    identities = {s["id"] for s in originals}
    result = []
    key = catalog["session"]
    for order, revision in enumerate(catalog["canonical"]["revisions"]):
        continuity = revision.get("continuity", {})
        context = continuity.get("context", {})
        records = [
            {
                "id": "0",
                "role": "summary_metadata",
                "text": json.dumps(
                    {
                        "revision_id": revision["id"],
                        "created_at": revision["created_at"],
                        "provenance": "derived_summary",
                        "prior_revision": revision.get("prior_revision"),
                        "coverage": {
                            k: continuity.get(k)
                            for k in (
                                "available_records",
                                "summarized_records",
                                "remaining_records",
                            )
                        },
                    },
                    ensure_ascii=False,
                ),
            }
        ]
        refs: dict[str, list[str]] = {}
        for field in memory_summary.FIELDS:
            for index, claim in enumerate(context.get(field, [])):
                identity = field + ":" + str(index)
                refs[identity] = claim["refs"]
                records.append(
                    {
                        "id": identity,
                        "role": "derived_claim",
                        "text": json.dumps(
                            {
                                "text": claim["text"],
                                "refs": claim["refs"],
                                "provenance": "derived_summary",
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
        summary = {"id": "summary", "role": "derived_summary", "text": revision["summary"]}
        records.append(summary)
        value = projection(
            key,
            "revision",
            revision["id"],
            "Summary revision",
            records,
            revision_id=revision["id"],
            prior_revision=revision.get("prior_revision"),
            created_at=revision["created_at"],
            revision_order=order,
            claim_refs=refs,
            processed=continuity.get("processed", {}),
        )
        # Version is immutable even when unavailable records are withheld.
        visible = {
            identity
            for identity, evidence in refs.items()
            if evidence and all(ref in available for ref in evidence)
        }
        summary_allowed = bool(
            continuity.get("verified")
            and set(continuity.get("processed", {})) <= identities
            and len(visible) == len(refs)
        )
        value["records"] = [
            r
            for r in records
            if r["id"] == "0" or r["id"] in visible or (r["id"] == "summary" and summary_allowed)
        ]
        result.append(value)
    for cp in catalog["canonical"]["checkpoints"]:
        record = cp["record"]
        saved_refs = record.get("memory_sources", []) + record.get("retained_sources", [])
        if any(r.get("root", catalog["root"]) != catalog["root"] for r in saved_refs):
            continue
        text = json.dumps(
            {
                "id": cp["id"],
                "label": cp["label"],
                "created_at": cp["created_at"],
                "summary_ref": record.get("summary_ref"),
                "events": record.get("events"),
                "git": record.get("git"),
                "conversation": record.get("conversation"),
                "provenance": "checkpoint_marker",
            },
            ensure_ascii=False,
        )
        result.append(
            projection(
                key,
                "checkpoint",
                cp["id"],
                "Checkpoint: " + cp["label"],
                [{"id": "0", "role": "checkpoint_marker", "text": text}],
                checkpoint_id=cp["id"],
                summary_ref=record.get("summary_ref"),
            )
        )
    return result
