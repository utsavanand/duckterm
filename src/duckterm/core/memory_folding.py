"""Optional on-write compaction through the existing revision writer.

Providers run before the write transaction. Parents are immutable revisions with
claim pointers; committed parent links define the active set. No rows are deleted.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import Any

from duckterm import memory_compaction, memory_contract, memory_summary
from duckterm.memory_provider import CALL_TIMEOUT
from duckterm.persistence.saved_state import REVISION_BUCKET, fingerprint

DEADLINE_SECONDS = 2 * CALL_TIMEOUT
RETRY_MS = 5 * 60 * 1000
MAX_FOLDS = 8


def rows(store: Any, key: str, scope: str) -> list[dict[str, Any]]:
    result = []
    for identity, text, created_at in store._conn.execute(
        "SELECT id,text,created_at FROM digest_items WHERE session_key=? AND bucket=? "
        "ORDER BY created_at,rowid",
        (key, REVISION_BUCKET),
    ):
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("invalid_saved_revision")
        if value.get("source", {}).get("scope_hash") == scope:
            result.append({**value, "id": identity, "created_at": created_at})
    return result


def active(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Legacy cumulative rows never acquire inferred span boundaries."""
    seen: dict[str, dict[str, Any]] = {}
    consumed: set[str] = set()
    for row in records:
        if row.get("span", "cumulative") != "incremental":
            continue
        fold = row.get("fold", {"tier": 0, "children": []})
        tier, children = fold.get("tier"), fold.get("children")
        if type(tier) is not int or tier < 0 or not isinstance(children, list):
            raise ValueError("invalid_fold_history")
        if tier == 0:
            if children:
                raise ValueError("invalid_fold_history")
        elif (
            len(children) != memory_compaction.WIDTH
            or any(not isinstance(child, str) for child in children)
            or len(set(children)) != len(children)
            or any(child not in seen or child in consumed for child in children)
            or any(seen[child]["tier"] != tier - 1 for child in children)
        ):
            raise ValueError("invalid_fold_history")
        consumed.update(children)
        seen[row["id"]] = {"id": row["id"], "tier": tier, "span": "incremental"}
    return [node for identity, node in seen.items() if identity not in consumed]


def _node(row: dict[str, Any], index: dict[str, dict[str, Any]], tier: int) -> dict[str, Any]:
    if row.get("summary_validation", {}).get("ready") is not True:
        raise ValueError("unverified_fold_input")
    context, origins = memory_contract.resolve_revision(row, index.get)
    return {
        "id": row["id"],
        "tier": tier,
        "span": row.get("span", "cumulative"),
        "context_version": row.get("context_version", 1),
        "coverage": row.get("coverage"),
        "context": context,
        "origins": origins,
        "revision_hash": fingerprint(row),
    }


async def prepare(
    coordinator: Any, key: str, captured: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any] | None:
    """Stage folds before a typed leaf append; failures never reject that leaf."""
    if incoming.get("span", "cumulative") != "incremental":
        return None
    if incoming.get("context_version") != 2:
        return {"state": "pending", "reason": "unsupported_context_version"}
    scope = captured["source"]["scope_hash"]
    now = int(time.time() * 1000)
    try:
        existing = rows(coordinator.server.digests, key, scope)
        heads = active(existing)
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        return {"state": "pending", "reason": "invalid_fold_history"}
    if not memory_compaction.select(heads, 0):
        return None
    index = {row["id"]: row for row in existing}
    proposal: dict[str, Any] = {
        "state": "prepared",
        "base_hash": fingerprint(existing),
        "scope": scope,
        "parents": [],
        "attempts": [],
    }

    async def provider(prompt: str) -> str:
        from duckterm.server import summarize

        reply = await asyncio.to_thread(summarize, prompt)
        if getattr(reply, "failure_reason", None):
            raise RuntimeError("compaction_provider_failed")
        return reply.text

    async def fold(tier: int) -> None:
        selected = memory_compaction.select(heads, tier)
        if not selected:
            return
        attempt: dict[str, Any] = {
            "input_key": fingerprint([index[node["id"]] for node in selected]),
            "state": "pending",
            "reason": "deadline",
            "retry_after": now + RETRY_MS,
        }
        proposal["attempts"].append(attempt)
        previous = next(
            (
                item
                for row in reversed(existing)
                for item in row.get("compaction", {}).get("attempts", [])
                if item.get("input_key") == attempt["input_key"]
            ),
            None,
        )
        if previous and previous.get("state") == "pending" and previous.get("retry_after", 0) > now:
            attempt.update(reason="retry_deferred", retry_after=previous["retry_after"])
            return
        if len(proposal["attempts"]) > MAX_FOLDS:
            attempt["reason"] = "fold_budget_exhausted"
            return
        try:
            children = [_node(index[node["id"]], index, tier) for node in selected]
            result = await memory_compaction.compact(
                children, provider, provider, timeout=CALL_TIMEOUT
            )
        except memory_contract.BudgetExceeded as exc:
            attempt.update(reason="budget_exhausted", coverage=exc.coverage)
            return
        except (ValueError, KeyError, TypeError, AttributeError):
            attempt["reason"] = "unreadable_fold_input"
            return
        if result["state"] != "compacted":
            attempt.update(reason=result["reason"])
            if "coverage" in result:
                attempt["coverage"] = result["coverage"]
            return
        origins: dict[str, str] = {}
        for child in children:
            for identity, origin in child["origins"].items():
                origins.setdefault(identity, origin)
        # Higher tiers also fold BEFORE the new parent at that tier exists.
        await fold(tier + 1)
        parent = {
            "source": {"scope_hash": scope},
            "conversation": captured["conversation"],
            "policy": captured["policy"],
            "source_at": now,
            "input_key": "fold:" + attempt["input_key"],
            "trigger": {
                "kind": "compaction",
                "initiator": captured.get("trigger", {"kind": "internal_refresh"}),
            },
            "summary": result["context"]["overview"],
            "context_version": 2,
            "span": "incremental",
            "coverage": result["coverage"],
            "fold": {
                "tier": result["tier"],
                "children": result["children"],
                "input_hash": result["input_hash"],
            },
            "continuity": {
                "verified": True,
                "context": {
                    "overview": result["context"]["overview"],
                    **{
                        field: [
                            {"id": claim["id"], "ref_revision": origins[claim["id"]]}
                            for claim in result["context"][field]
                        ]
                        for field in memory_summary.FIELDS
                    },
                },
            },
            "summary_validation": {"ready": True, "reason_codes": []},
        }
        proposal["parents"].append(parent)
        attempt.update(state="compacted", reason=None, retry_after=0)
        child_ids = set(result["children"])
        heads[:] = [node for node in heads if node["id"] not in child_ids]
        heads.append({"id": "staged:" + attempt["input_key"], "tier": tier + 1})

    try:
        async with asyncio.timeout(DEADLINE_SECONDS):
            await fold(0)
    except TimeoutError:
        proposal["state"] = "pending"
    except (ValueError, TypeError, KeyError, AttributeError):
        proposal["state"] = "pending"
        proposal["reason"] = "invalid_fold_input"
    if any(a["state"] == "pending" for a in proposal["attempts"]):
        proposal["state"] = "pending"
    return proposal


def apply(store: Any, key: str, proposal: dict[str, Any], now: int) -> dict[str, Any]:
    """Called only inside the coordinator's transaction, before the incoming leaf.

    Roll back optional parents alone on failure. A checkpoint can still commit;
    the original children remain available and active until parent links commit.
    """
    status = {k: v for k, v in proposal.items() if k in ("state", "reason", "attempts")}
    if not proposal.get("parents"):
        return status
    conn = store._conn
    if not conn.in_transaction:
        raise RuntimeError("compaction requires the shared write transaction")
    conn.execute("SAVEPOINT memory_compaction")
    try:
        if fingerprint(rows(store, key, proposal["scope"])) != proposal["base_hash"]:
            status.update(state="pending", reason="source_changed")
        else:
            status["parent_ids"] = [
                store.add_revision(key, parent, now) for parent in proposal["parents"]
            ]
            if status["state"] == "prepared":
                status["state"] = "compacted"
    except Exception:
        conn.execute("ROLLBACK TO memory_compaction")
        status.update(state="pending", reason="persistence_failed")
        status.pop("parent_ids", None)
    finally:
        conn.execute("RELEASE memory_compaction")
    if "parent_ids" not in status:
        status["attempts"] = [
            (
                {**attempt, "state": "pending", "reason": status["reason"], "retry_after": 0}
                if attempt["state"] == "compacted"
                else attempt
            )
            for attempt in status.get("attempts", [])
        ]
    return status
