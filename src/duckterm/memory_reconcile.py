"""Reconcile span items into one current memory: new, duplicate, merged, done, superseded.

Owner, 2026-10-30: span summaries are the history and are never reconciled. Items
(intent, rules, facts) are reconciled after every checkpoint. Silence keeps an
item; closing one needs a later span summary or item as evidence. Each run
saves a new row, so earlier item sets stay readable.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from duckterm import memory_contract, memory_summary, prompts
from duckterm.core import progress
from duckterm.persistence.saved_state import transaction

BUCKET = "memory_items_v1"
# Claude Code returns this as validated structured output, so a reply always
# parses; apply() still checks every value. Plain-text replies from 2 of 8
# replayed checkpoints did not parse (2026-11-01).
REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["duplicate", "merge", "done", "superseded", "drop"],
                    },
                    "id": {"type": "string"},
                    "of": {"type": "string"},
                    "by": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "kind": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["op"],
            },
        }
    },
    "required": ["ops"],
}
MAX_ITEM_CHARS = 300
Provider = Callable[[str], Awaitable[str]]


def latest(store: Any, key: str, scope: str) -> dict[str, Any] | None:
    for identity, text in store._conn.execute(
        "SELECT id,text FROM digest_items WHERE session_key=? AND bucket=? "
        "ORDER BY created_at DESC,rowid DESC",
        (key, BUCKET),
    ):
        value = json.loads(text)
        if value.get("scope_hash") == scope:
            return {**value, "id": identity}
    return None


def leaf_items(leaf: dict[str, Any]) -> list[dict[str, Any]]:
    context = leaf.get("continuity", {}).get("context", {})
    return [
        {
            "id": claim["id"],
            "kind": memory_contract.LEGACY_KINDS.get(claim["kind"], claim["kind"]),
            "text": claim["text"],
            "refs": claim["refs"],
            "since": leaf["id"],
        }
        for field in memory_summary.FIELDS
        for claim in context.get(field, [])
        if claim.get("state", "open") == "open" and "id" in claim and "text" in claim
    ]


def pending(leaves: list[dict[str, Any]], saved: dict[str, Any] | None) -> list[dict[str, Any]]:
    absorbed = set((saved or {}).get("absorbed", []))
    return [leaf for leaf in leaves if leaf["id"] not in absorbed]


def build_prompt(
    current: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    spans: list[dict[str, Any]],
) -> str:
    # Open items first: they change little between checkpoints, so the
    # provider can reuse the cached prefix; new items and summaries follow.
    payload = {
        "open_items": [{"id": i["ref"], "kind": i["kind"], "text": i["text"]} for i in current],
        "new_items": [{"id": i["ref"], "kind": i["kind"], "text": i["text"]} for i in candidates],
        "span_summaries": spans,
    }
    return prompts.render("reconcile", payload=json.dumps(payload, ensure_ascii=False))


def apply(
    ops: Any,
    items: dict[str, dict[str, Any]],
    order: list[str],
    spans: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Return kept items, closed records and the count of rejected operations.

    An invalid operation is skipped, which keeps its item: the safe default.
    """
    used: set[str] = set()
    closed: list[dict[str, Any]] = []
    merged: dict[str, dict[str, Any]] = {}
    rejected = 0
    position = {ref: index for index, ref in enumerate(order)}
    for op in ops if isinstance(ops, list) else []:
        # A parsed reply can still carry scalars or lists where ids belong
        # (main-qa, #364); reject those before any set or dict lookup.
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            rejected += 1
            continue
        kind, subject = op["op"], op.get("id")
        if kind == "merge":
            ids = op.get("ids")
            text = op.get("text")
            new_kind = op.get("kind")
            if (
                not isinstance(ids, list)
                or any(not isinstance(i, str) for i in ids)
                or len(set(ids)) < 2
                or any(i not in items or i in used for i in ids)
                or not isinstance(text, str)
                or not text.strip()
                or len(text) > MAX_ITEM_CHARS
                or new_kind not in memory_contract.KIND_FIELD
            ):
                rejected += 1
                continue
            ids = sorted(set(ids), key=position.__getitem__)
            refs = sorted({ref for i in ids for ref in items[i]["refs"]})
            field = memory_contract.KIND_FIELD[new_kind]
            item = {
                "id": memory_contract.claim_id(field, text.strip(), refs),
                "kind": new_kind,
                "text": text.strip(),
                "refs": refs,
                "since": items[ids[-1]]["since"],
                "merged_from": [items[i]["id"] for i in ids],
            }
            merged[ids[0]] = item
            used.update(ids)
            closed += [{**items[i], "op": "merged", "by": item["id"]} for i in ids]
            continue
        raw_target = op.get("of") if kind == "duplicate" else op.get("by")
        if not isinstance(subject, str) or not isinstance(raw_target, str | None):
            rejected += 1
            continue
        # "" is never an id, span or other item, so a missing target fails lookups.
        target = raw_target or ""
        valid = (
            kind in {"duplicate", "done", "superseded", "drop"}
            and subject in items
            and subject not in used
        )
        if kind == "drop":
            # Only a new candidate may fail the bar; an open item needs evidence.
            valid = valid and items[subject]["new"]
        elif kind == "done":
            valid = valid and (target in spans or (target in items and target != subject))
        elif valid:
            valid = target in items and target != subject and target not in used
            if kind == "superseded":
                valid = valid and position[target] > position[subject]
        if not valid:
            rejected += 1
            continue
        used.add(subject)
        evidence = spans.get(target) or (items[target]["id"] if target in items else None)
        closed.append({**items[subject], "op": kind, "by": evidence})
    kept = []
    for ref in order:
        if ref in merged:
            kept.append(merged[ref])
        elif ref not in used:
            kept.append(items[ref])
    for item in [*kept, *closed]:
        item.pop("new", None)
        item.pop("ref", None)
    return kept, closed, rejected


async def reconcile(
    store: Any,
    key: str,
    scope: str,
    leaves: list[dict[str, Any]],
    generate: Provider,
) -> dict[str, Any]:
    """Absorb every leaf not yet reconciled in one provider call.

    One call over every item: three per-category calls each re-read the whole
    list as cross-category context, and reconcile was ~75% of checkpoint cost.
    """
    saved = latest(store, key, scope)
    new_leaves = pending(leaves, saved)
    if not new_leaves:
        return {"state": "current", "id": saved["id"] if saved else None}
    old = [dict(item) for item in (saved or {}).get("items", [])]
    seen = {item["id"] for item in old}
    new = []
    for leaf in new_leaves:
        for item in leaf_items(leaf):
            # The same text and refs re-emitted by a later span is the same item.
            if item["id"] not in seen:
                seen.add(item["id"])
                new.append(item)
    spans = {f"s{n}": leaf["id"] for n, leaf in enumerate(new_leaves, 1)}
    summaries = [
        {"id": f"s{n}", "summary": leaf.get("summary", "")} for n, leaf in enumerate(new_leaves, 1)
    ]
    for n, item in enumerate(old, 1):
        item.update(ref=f"o{n}", new=False)
    for n, item in enumerate(new, 1):
        item.update(ref=f"n{n}", new=True)
    items = {i["ref"]: i for i in [*old, *new]}
    order = [i["ref"] for i in [*old, *new]]
    kept: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    rejected = 0
    if items:
        prompt = build_prompt(old, new, summaries)
        for _ in range(2):
            raw = progress.json_object(await generate(prompt))
            if raw is not None and isinstance(raw.get("ops"), list):
                kept, closed, rejected = apply(raw["ops"], items, order, spans)
                break
        else:
            raise ValueError("invalid_reconcile_reply")
    value = {
        "format": BUCKET,
        "scope_hash": scope,
        "base": saved["id"] if saved else None,
        "absorbed": [*(saved or {}).get("absorbed", []), *(leaf["id"] for leaf in new_leaves)],
        "items": kept,
        "closed": closed,
        "rejected_ops": rejected,
    }
    identity = uuid.uuid4().hex
    conn = store._conn
    with transaction(conn):
        if (latest(store, key, scope) or {}).get("id") != value["base"]:
            raise ValueError("reconcile_base_changed")
        now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO digest_items VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (identity, key, BUCKET, json.dumps(value), now, now),
        )
    return {
        "state": "reconciled",
        "id": identity,
        "items": len(kept),
        "closed": len(closed),
        "rejected_ops": value["rejected_ops"],
    }


def brief_items(
    saved: dict[str, Any] | None, leaves: list[dict[str, Any]], closed: set[str]
) -> dict[str, list[dict[str, Any]]]:
    """Current items by category; leaves not yet reconciled add their items unmerged.

    closed holds ids retired or superseded through the explicit lifecycle API.
    """
    result: dict[str, list[dict[str, Any]]] = {name: [] for name in memory_contract.CATEGORIES}
    items = [*(saved or {}).get("items", [])]
    items += [item for leaf in pending(leaves, saved) for item in leaf_items(leaf)]
    seen: set[str] = set(closed)
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        result[memory_contract.category(item["kind"])].append(
            {"id": item["id"], "kind": item["kind"], "text": item["text"], "refs": item["refs"]}
        )
    return result
