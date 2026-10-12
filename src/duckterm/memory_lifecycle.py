"""Explicit claim transitions using existing evidenced links, never summary inference.

Original revisions remain immutable. Only versioned lifecycle links written by
the authenticated owning session affect this projection; legacy annotations do not.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from duckterm import memory_graph, memory_summary
from duckterm.core.session_api import APIError


def transitions(links: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for link in links:
        if link.get("lifecycle_version") != 1:
            continue
        identity = link["target_id"]
        if identity in result and result[identity]["id"] != link["id"]:
            raise ValueError("conflicting_claim_transition")
        result[identity] = link
    for identity in result:
        visited: set[str] = set()
        node: str | None = identity
        while node in result:
            if node in visited:
                raise ValueError("supersession_cycle")
            visited.add(node)
            node = result[node].get("replacement_id")
    return result


def request(body: Any, sources: list[dict[str, Any]], catalog: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "operation",
        "target",
        "target_id",
        "replacement",
        "replacement_id",
        "evidence",
        "request_key",
    }
    if (
        not isinstance(body, dict)
        or set(body) != fields
        or body["operation"] not in {"retire", "supersede"}
        or not isinstance(body["request_key"], str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", body["request_key"])
        or not isinstance(body["evidence"], list)
        or not 1 <= len(body["evidence"]) <= 16
    ):
        raise APIError(400, "Supply an explicit claim transition, evidence and request_key")
    own_revisions = {row["id"] for row in catalog["canonical"]["revisions"]}

    def claim(ref: Any, identity: Any) -> None:
        source, record = memory_graph.resolve(sources, ref)
        if (
            source["kind"] != "revision"
            or source.get("revision_id") not in own_revisions
            or record is None
            or record["role"] != "derived_claim"
        ):
            raise APIError(403, "Only this session's own typed claims may change")
        value = json.loads(record["text"])
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"[a-f0-9]{16}", identity)
            or value.get("id") != identity
            or value.get("kind") == "unclassified"
        ):
            raise APIError(400, "Target must identify an exact typed claim")
        if value.get("state", "open") != "open":
            raise APIError(409, "Claim already has a terminal state")

    claim(body["target"], body["target_id"])
    if body["operation"] == "retire":
        if body["replacement"] is not None or body["replacement_id"] is not None:
            raise APIError(400, "Retirement has no replacement")
    else:
        claim(body["replacement"], body["replacement_id"])
        if body["target_id"] == body["replacement_id"]:
            raise APIError(400, "A claim cannot supersede itself")
    for ref in body["evidence"]:
        source, record = memory_graph.resolve(sources, ref)
        if (
            source["kind"] not in {"conversation", "event", "session", "task", "inbox", "artifact"}
            or record is None
            or record["role"].startswith("derived_")
        ):
            raise APIError(
                400, "Lifecycle evidence must be an original record in this session's scope"
            )
    return {
        "lifecycle_version": 1,
        "relation": "claim_retired" if body["operation"] == "retire" else "claim_superseded",
        "from": body["target"],
        "to": body["replacement"] or body["target"],
        "target_id": body["target_id"],
        "replacement_id": body["replacement_id"],
        "evidence": body["evidence"],
        "request_key": body["request_key"],
    }


def validate_new(value: dict[str, Any], links: list[dict[str, Any]]) -> None:
    try:
        current = transitions(links)
        old = current.get(value["target_id"])
        if old is not None and old["id"] != value["id"]:
            raise ValueError("claim_already_retired_or_superseded")
        if value.get("replacement_id") in current:
            raise ValueError("replacement_is_not_open")
        transitions([*links, value])
    except ValueError as exc:
        raise APIError(409, str(exc)) from exc


def effective(
    claim: dict[str, Any], links: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    """Current read metadata; the saved claim text and version stay immutable."""
    saved = claim.get("state", "open")
    link = transitions(links).get(str(claim.get("id", "")))
    result: dict[str, Any] = {
        "saved_state": saved,
        "state": saved,
        "state_provenance": "saved_revision",
    }
    if link is None:
        return result
    result.update(state_provenance="explicit_claim_transition", transition_id=link["id"])
    try:
        for ref in link["evidence"]:
            memory_graph.resolve(sources, ref)
    except APIError:
        return {**result, "state": "unknown", "state_reason": "transition_evidence_unavailable"}
    return {
        **result,
        "state": "retired" if link["relation"] == "claim_retired" else "superseded",
        "replacement": link.get("to") if link.get("replacement_id") else None,
        "replacement_id": link.get("replacement_id"),
    }


def project(
    context: dict[str, Any], links: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    """A missing transition's evidence blocks assembly; it cannot resurrect a claim."""
    values = transitions(links)
    result = deepcopy(context)
    for field in memory_summary.FIELDS:
        for claim in result[field]:
            link = values.get(claim.get("id"))
            if link is None:
                continue
            for ref in link["evidence"]:
                memory_graph.resolve(sources, ref)
            claim["state"] = "retired" if link["relation"] == "claim_retired" else "superseded"
    return result
