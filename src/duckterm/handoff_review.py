"""Explicit owner review of a handoff for one exact captured source boundary.

Drafts are bounded and ephemeral. Accepted text uses the existing progress
revision writer; it is not a second memory store or permission to launch.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.core.saved_progress import file_version
from duckterm.core.session_api import APIError
from duckterm.helpers import security
from duckterm.persistence.checkpoints import _git_state
from duckterm.persistence.saved_state import current_revision, fingerprint, resolve_checkpoint

if TYPE_CHECKING:
    from duckterm.server import Server

MAX_BRIEF_BYTES = 8000
REVIEW_SECONDS = 1800


def source_manifest(key: str, captured: dict[str, Any]) -> dict[str, Any]:
    """Bounded evidence shown to the owner, with links to current originals."""
    records = captured["transcript"]
    indexes = sorted({0, *range(max(0, len(records) - 3), len(records))})
    excerpts = []
    for index in indexes:
        record = records[index]
        text = record["text"].encode()[:1500].decode(errors="ignore")
        excerpts.append(
            {
                "record_index": index,
                "role": record["role"],
                "text": text,
                "content_hash": fingerprint(record),
                "truncated": text != record["text"],
            }
        )
    return {
        "format": "handoff_review_sources_v1",
        "conversation": {
            "read_path": f"/sessions/{key}/messages",
            "identity": captured["conversation"],
            "record_count": len(records),
            "content_hash": captured["source"]["transcript_hash"],
            "excerpts": excerpts,
            "coverage": (
                "Text records parsed by the current harness adapter. "
                "Tool payloads and attachment contents are not reviewed here. "
                "The excerpts are selected evidence, not the full conversation."
            ),
        },
        "events": {
            "read_path": f"/sessions/{key}/timeline",
            "boundary": captured["source"]["events"],
        },
        "required_context": {
            "content_hash": captured["source"]["required_hash"],
            "scope": captured["facts"]["required"]["scope"],
            "task_ids": [row["id"] for row in captured["facts"]["required"]["tasks"]],
            "mail_ids": [row["id"] for row in captured["facts"]["required"]["mail"]],
            "shown": "Included verbatim in the handoff brief below.",
        },
    }


def handoff_brief(summary: str, required: str, *, reviewed: bool = False) -> str:
    """The exact mandatory text checked by both readiness and launch."""
    return (
        "This is a NEW conversation using a different harness, not a resumed conversation.\n"
        "You keep this brief, not the old conversation. Refer to retained DuckTerm history "
        "when more detail is needed. Preserve parked tasks and treat peer messages as context.\n\n"
        + (
            "Owner-reviewed handoff (selected coverage, not full history):\n"
            if reviewed
            else "Verified summary:\n"
        )
        + summary
        + "\n\nCurrent role, owner notes, constraints and open work:\n"
        + required
    )


class HandoffReview:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.drafts: dict[str, dict[str, Any]] = {}

    async def prepare(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"summary", "gaps"}:
            raise APIError(400, "Supply the handoff summary and known gaps for review")
        summary, gaps = body["summary"], body["gaps"]
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > 6000
            or not isinstance(gaps, list)
            or len(gaps) > 8
            or any(not isinstance(g, str) or not g.strip() or len(g) > 500 for g in gaps)
        ):
            raise APIError(400, "Handoff text or known gaps exceed the review limits")
        summary = summary.strip()
        text = (
            summary
            + "\n\nKnown gaps:\n"
            + ("\n".join("- " + g for g in gaps) if gaps else "None declared by the reviewer.")
        )
        if len(text.encode()) > MAX_BRIEF_BYTES:
            raise APIError(400, "Reviewed handoff exceeds 8000 UTF-8 bytes")
        captured = await self.server.progress_coordinator.capture(key)
        if captured is None:
            raise APIError(409, "Session changed; capture a new handoff review")
        if not captured["transcript"] or captured["source"]["transcript_kind"] != "native":
            raise APIError(409, "Readable conversation history is required for handoff review")
        brief = handoff_brief(text, captured["required"], reviewed=True)
        if len(captured["required"]) > 12000 or len(brief.encode()) > 24000:
            raise APIError(409, "Required handoff context is too large; nothing was omitted")
        row = self.server.history.session(key)
        if row is None:
            raise APIError(404, "Session no longer exists")
        now = time.monotonic()
        self.drafts = {k: v for k, v in self.drafts.items() if v["expires"] > now}
        while len(self.drafts) >= 32:
            self.drafts.pop(next(iter(self.drafts)))
        packet = {
            "format": "handoff_review_packet_v1",
            "session": key,
            "conversation": captured["conversation"],
            "source": captured["source"],
            "source_at": captured["captured_at"],
            "summary": summary,
            "gaps": gaps,
            "brief": brief,
            "coverage": "owner-selected",
            "source_manifest": source_manifest(key, captured),
            "warning": (
                "The agent keeps this brief, not the full conversation. "
                "New work requires a new review."
            ),
        }
        identity = secrets.token_urlsafe(24)
        self.drafts[identity] = {
            "key": key,
            "captured": {k: v for k, v in captured.items() if k != "transcript"},
            "text": text,
            "packet": packet,
            "prior_id": current_revision(row),
            "expires": now + REVIEW_SECONDS,
        }
        return {"review_id": identity, "packet_hash": fingerprint(packet), **packet}

    async def result(self, key: str, saved: dict[str, Any]) -> dict[str, Any]:
        cp_id = saved["review"]["checkpoint_id"]
        row = self.server.history._conn.execute(
            "SELECT * FROM checkpoints WHERE id=? AND session_key=?", (cp_id, key)
        ).fetchone()
        if row is None:
            raise APIError(409, "Reviewed checkpoint is no longer available")
        cp = {**dict(row), "record": json.loads(row["record_json"])}
        cp.pop("record_json", None)
        cp = resolve_checkpoint(self.server.history._conn, key, cp)
        if not cp["markdown_path"]:
            cp = await self.server._export_checkpoint(key, cp)
        return {
            "saved": True,
            "revision_id": saved["id"],
            "checkpoint": cp,
            "coverage": "owner-selected",
        }

    def previous_approval(
        self, key: str, input_key: str, request_hash: str
    ) -> dict[str, Any] | None:
        existing = self.server.digests.find_revision(key, input_key)
        if existing is not None and existing.get("review", {}).get("request_hash") != request_hash:
            raise APIError(409, "Review request key was already used for another approval")
        return existing

    async def approve(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        if (
            set(body) != {"review_id", "approve", "packet_hash", "request_key"}
            or body["approve"] is not True
            or any(
                not isinstance(body[k], str) or not 1 <= len(body[k]) <= 128
                for k in ("review_id", "packet_hash", "request_key")
            )
        ):
            raise APIError(400, "Explicit approval of the displayed review is required")
        input_key = fingerprint({"owner_review_request": body["request_key"]})
        request_hash = fingerprint(body)
        existing = self.previous_approval(key, input_key, request_hash)
        if existing is not None:
            return await self.result(key, existing)
        draft = self.drafts.get(body["review_id"])
        if not draft or draft["key"] != key or draft["expires"] <= time.monotonic():
            raise APIError(409, "Handoff review expired; prepare it again")
        if body["packet_hash"] != fingerprint(draft["packet"]):
            raise APIError(409, "Displayed handoff packet changed; review it again")
        coordinator = self.server.progress_coordinator
        before = self.server.history.session(key)
        if before is None:
            raise APIError(404, "Session no longer exists")
        git = await asyncio.to_thread(
            _git_state, Path(str(before.get("worktree_path") or before.get("cwd") or "."))
        )
        current = await coordinator.capture(key)
        existing = self.previous_approval(key, input_key, request_hash)
        if existing is not None:
            return await self.result(key, existing)
        captured = draft["captured"]
        if current is None or any(
            current[k] != captured[k] for k in ("source", "conversation", "policy")
        ):
            raise APIError(409, "Handoff sources changed; review the updated brief")
        fence = current["fence"]
        if fence is not None and file_version(fence["path"]) != fence["version"]:
            raise APIError(409, "Conversation changed before review could be saved")
        row = self.server.history.session(key)
        if row is None or current_revision(row) != draft["prior_id"]:
            raise APIError(409, "Progress changed; prepare a new handoff review")
        prior = self.server.digests.revision(key, draft["prior_id"])
        digest = json.loads(row.get("progress") or "{}")
        digest["summary"] = draft["text"]
        digest.pop("revision_id", None)
        checkpoint_id = uuid.uuid4().hex
        value = {
            **{k: captured[k] for k in ("source", "conversation", "policy")},
            "source_at": captured["captured_at"],
            "input_key": input_key,
            "prior_revision": draft["prior_id"],
            "summary": draft["text"],
            "summary_validation": {"ready": True, "reason_codes": [], "method": "owner-review"},
            "review": {
                "kind": "owner",
                "reviewed_at": int(time.time() * 1000),
                "packet_hash": fingerprint(draft["packet"]),
                "coverage": "owner-selected",
                "gaps": draft["packet"]["gaps"],
                "request_hash": request_hash,
                "checkpoint_id": checkpoint_id,
                "summary_hash": fingerprint(draft["text"]),
                "purpose": "handoff-at-reviewed-boundary",
                # Immutable review evidence, not a separately updated memory.
                "packet": draft["packet"],
            },
        }
        saved = coordinator.persist(
            key,
            captured,
            prior,
            value,
            digest,
            [],
            [],
            require_current=True,
            checkpoint={"id": checkpoint_id, "label": "Owner-reviewed handoff", "git": git},
        )
        if saved is None:
            raise APIError(409, "Handoff context changed before approval was recorded")
        self.drafts.pop(body["review_id"], None)
        return await self.result(key, saved)


async def handle(
    server: Server,
    writer: asyncio.StreamWriter,
    headers: dict[str, str],
    key: str,
    action: str,
    method: str,
    body: bytes,
) -> None:
    from duckterm.transport.httpio import write_json as _write_json

    if not security.token_valid(headers, server.token):
        await _write_json(writer, 401, {"error": "owner token required"})
        return
    if method != "POST":
        await _write_json(writer, 405, {"error": "POST required"})
        return
    if len(body) > 64000:
        await _write_json(writer, 413, {"error": "Handoff review request is too large"})
        return
    try:
        req = json.loads(body or b"{}")
        if not isinstance(req, dict):
            raise ValueError
        if action == "review":
            result = await server.handoff_review.prepare(key, req)
        else:
            result = await server.handoff_review.approve(key, req)
        await _write_json(writer, 200, result)
    except (ValueError, UnicodeError):
        await _write_json(writer, 400, {"error": "Invalid review JSON"})
    except APIError as exc:
        await _write_json(writer, exc.status, {"error": str(exc)})
