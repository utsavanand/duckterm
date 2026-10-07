"""Typed relationships over canonical records; derived links never grant authority."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import fingerprint

HANDLE = re.compile(r"[a-f0-9]{32}:[a-f0-9]{64}")


def locator(source: dict[str, Any], record: str = "") -> dict[str, str]:
    return {"source": source["id"] + ":" + source["version"], "record": record}


def resolve(
    sources: list[dict[str, Any]], ref: Any
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if (
        not isinstance(ref, dict)
        or set(ref) != {"source", "record"}
        or not isinstance(ref["source"], str)
        or not HANDLE.fullmatch(ref["source"])
        or not isinstance(ref["record"], str)
        or len(ref["record"]) > 1024
    ):
        raise APIError(400, "Supply an exact source handle and record ID")
    source = next((s for s in sources if s["id"] + ":" + s["version"] == ref["source"]), None)
    if source is None:
        raise APIError(409, "Memory source changed or was removed; search again")
    record = next((r for r in source["records"] if r["id"] == ref["record"]), None)
    if ref["record"] and record is None:
        raise APIError(404, "Memory record is unavailable")
    return source, record


def request(body: Any, sources: list[dict[str, Any]]) -> dict[str, Any]:
    if (
        not isinstance(body, dict)
        or set(body) != {"from", "relation", "to", "evidence", "request_key"}
        or not isinstance(body["request_key"], str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", body["request_key"])
        or body["relation"] not in ("produced", "supports", "supersedes")
        or not isinstance(body["evidence"], list)
        or not 1 <= len(body["evidence"]) <= 16
    ):
        raise APIError(
            400, "Invalid memory link; supply a typed relationship, evidence and request_key"
        )
    left, left_record = resolve(sources, body["from"])
    right, right_record = resolve(sources, body["to"])
    for ref in body["evidence"]:
        _, record = resolve(sources, ref)
        if record is None or record["role"] in {
            "derived_context",
            "derived_claim",
            "derived_summary",
            "summary_metadata",
            "checkpoint_marker",
            "derived_progress",
        }:
            raise APIError(400, "Link evidence must identify an original record")
    claim_left = left_record is not None and left_record["role"] == "derived_claim"
    claim_right = right_record is not None and right_record["role"] == "derived_claim"
    valid = (
        body["relation"] == "produced"
        and left["kind"] == "task"
        and right["kind"] in {"artifact", "attachment"}
    )
    valid |= (
        body["relation"] == "supports"
        and claim_right
        and left_record is not None
        and left["kind"] in {"conversation", "session", "event", "task", "inbox", "artifact"}
        and left_record["role"] != "derived_context"
        and body["from"] in body["evidence"]
    )
    valid |= (
        body["relation"] == "supersedes"
        and claim_left
        and claim_right
        and left["revision_order"] > right["revision_order"]
    )
    if not valid:
        raise APIError(400, "Relationship endpoints have incompatible types or chronology")
    return body


def edges(catalog: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = {(s["id"] + ":" + s["version"], r["id"]): r for s in sources for r in s["records"]}
    handles = {s["id"] + ":" + s["version"] for s in sources}
    result: dict[str, dict[str, Any]] = {}
    record_hashes: dict[tuple[str, str], str] = {}

    def allowed(ref: dict[str, str]) -> bool:
        return ref["source"] in handles and (
            not ref["record"] or (ref["source"], ref["record"]) in records
        )

    def add(
        kind: str,
        left: dict[str, str],
        right: dict[str, str],
        evidence: list[dict[str, str]],
        provenance: dict[str, Any],
        identity: str | None = None,
    ) -> None:
        if not all(allowed(ref) for ref in [left, right, *evidence]):
            return
        edge = {
            "relation": kind,
            "from": left,
            "to": right,
            "evidence": evidence,
            "provenance": provenance,
        }
        identity = identity or fingerprint(edge)
        result[identity] = {"id": identity, **edge}

    structural = {"kind": "server_structure", "author": "duckterm", "authority": "none"}
    session = next((s for s in sources if s["kind"] == "session" and not s.get("retained")), None)
    revisions = {s["revision_id"]: s for s in sources if s["kind"] == "revision"}
    for source in sources:
        root = locator(source)
        if session and source is not session:
            add("belongs_to_session", root, locator(session, "0"), [], structural)
        if source["kind"] == "revision":
            prior = revisions.get(source.get("prior_revision"))
            if prior:
                add("continues_revision", root, locator(prior), [], structural)
            for record in source["records"]:
                if record["role"] != "derived_claim":
                    continue
                claim = locator(source, record["id"])
                add("contains", root, claim, [], structural)
                for ref in source["claim_refs"][record["id"]]:
                    identity, version, record_id = ref.split(":", 2)
                    evidence = {"source": identity + ":" + version, "record": record_id}
                    add(
                        "supports",
                        evidence,
                        claim,
                        [evidence],
                        {"kind": "summary_pipeline", "author": "duckterm", "authority": "derived"},
                    )
            for (handle, record_id), record in records.items():
                identity = handle.split(":", 1)[0]
                expected = source["processed"].get(identity, {}).get(record_id)
                if expected is None:
                    continue
                key = (handle, record_id)
                if key not in record_hashes:
                    record_hashes[key] = fingerprint([record_id, record["role"], record["text"]])
                if expected == record_hashes[key]:
                    add("processed", root, {"source": handle, "record": record_id}, [], structural)
        elif source["kind"] == "checkpoint":
            revision = revisions.get(source.get("summary_ref"))
            if revision:
                add("references_revision", locator(source, "0"), locator(revision), [], structural)
    for link in catalog["canonical"]["links"]:
        add(
            link["relation"],
            link["from"],
            link["to"],
            link["evidence"],
            link["provenance"],
            link["id"],
        )
    return [result[key] for key in sorted(result)]


def related(
    catalog: dict[str, Any],
    sources: list[dict[str, Any]],
    handle: str,
    record: str,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    all_edges = edges(catalog, sources)
    matching = []
    for edge in all_edges:
        directions = [
            side
            for side in ("from", "to")
            if edge[side]["source"] == handle and (not record or edge[side]["record"] == record)
        ]
        if directions:
            matching.append(
                {**edge, "direction": "outgoing" if directions[0] == "from" else "incoming"}
            )
    stamp = fingerprint(
        [catalog, [(s["id"], s["version"]) for s in sources], handle, record, limit, all_edges]
    )
    offset = 0
    if cursor is not None:
        try:
            if len(cursor) > 1024:
                raise ValueError
            data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if (
                set(data) != {"snapshot", "offset"}
                or type(data["offset"]) is not int
                or not 0 <= data["offset"] <= len(matching)
            ):
                raise ValueError
            if data["snapshot"] != stamp:
                raise APIError(409, "Memory graph or access changed; start traversal again")
            offset = data["offset"]
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise APIError(400, "Invalid memory graph cursor") from exc
    end = offset + limit
    next_cursor = (
        base64.urlsafe_b64encode(json.dumps({"snapshot": stamp, "offset": end}).encode()).decode()
        if end < len(matching)
        else None
    )
    return {"relations": matching[offset:end], "next_cursor": next_cursor}
