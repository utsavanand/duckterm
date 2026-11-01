"""Reconcile span items into one current memory: new, duplicate, merged, done, superseded.

Owner, 2026-10-30: span summaries are the history and are never reconciled. Items
(intent, rules, facts) are reconciled after every checkpoint. Silence keeps an
item; closing one needs a later span summary or item as evidence. Each run
saves a new row, so earlier item sets stay readable.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from duckterm import memory_contract, memory_summary, prompts
from duckterm.core import progress
from duckterm.persistence.saved_state import transaction

BUCKET = "memory_items_v1"
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
    category: str,
    current: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    others: dict[str, dict[str, Any]],
) -> str:
    payload = {
        "category": category,
        "kinds": list(memory_contract.CATEGORIES[category]),
        "open_items": [{"id": i["ref"], "kind": i["kind"], "text": i["text"]} for i in current],
        "new_items": [{"id": i["ref"], "kind": i["kind"], "text": i["text"]} for i in candidates],
        "span_summaries": spans,
        "other_category_items": [
            {"id": ref, "kind": i["kind"], "text": i["text"]} for ref, i in others.items()
        ],
    }
    return prompts.render("reconcile", payload=json.dumps(payload, ensure_ascii=False))


def apply(
    ops: Any,
    items: dict[str, dict[str, Any]],
    order: list[str],
    spans: dict[str, str],
    category: str,
    others: dict[str, dict[str, Any]] | None = None,
    rank: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Return kept items, closed records and the count of rejected operations.

    others are read-only items of other categories, citable as evidence; rank
    orders leaves so a cross-category supersession must come from a later span.
    An invalid operation is skipped, which keeps its item: the safe default.
    """
    others, rank = others or {}, rank or {}
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
                or new_kind not in memory_contract.CATEGORIES[category]
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
            valid = valid and (
                target in spans or target in others or (target in items and target != subject)
            )
        elif valid and kind == "superseded" and target in others:
            valid = rank.get(others[target]["since"], -1) >= rank.get(items[subject]["since"], -1)
        elif valid:
            valid = target in items and target != subject and target not in used
            if kind == "superseded":
                valid = valid and position[target] > position[subject]
        if not valid:
            rejected += 1
            continue
        used.add(subject)
        evidence = spans.get(target) or (
            items[target]["id"]
            if target in items
            else others[target]["id"] if target in others else None
        )
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
    """Absorb every leaf not yet reconciled, one provider call per category."""
    saved = latest(store, key, scope)
    new_leaves = pending(leaves, saved)
    if not new_leaves:
        return {"state": "current", "id": saved["id"] if saved else None}
    current = [dict(item) for item in (saved or {}).get("items", [])]
    candidates = [item for leaf in new_leaves for item in leaf_items(leaf)]
    spans = {f"s{n}": leaf["id"] for n, leaf in enumerate(new_leaves, 1)}
    summaries = [
        {"id": f"s{n}", "summary": leaf.get("summary", "")} for n, leaf in enumerate(new_leaves, 1)
    ]

    rank = {leaf["id"]: n for n, leaf in enumerate(leaves)}
    everything: dict[str, dict[str, Any]] = {}
    for item in [*current, *candidates]:
        everything.setdefault(item["id"], item)

    async def run(category: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        # Contradictions cross categories (a fact can retire a rule), so every
        # call sees the other categories read-only, and runs whenever anything
        # is new.
        others = {
            f"c{n}": item
            for n, item in enumerate(
                (i for i in everything.values() if memory_contract.category(i["kind"]) != category),
                1,
            )
        }
        old = [i for i in current if memory_contract.category(i["kind"]) == category]
        seen = {i["id"] for i in old}
        new = []
        for item in candidates:
            # The same text and refs re-emitted by a later span is the same item.
            if memory_contract.category(item["kind"]) == category and item["id"] not in seen:
                seen.add(item["id"])
                new.append(dict(item))
        for n, item in enumerate(old, 1):
            item.update(ref=f"o{n}", new=False)
        for n, item in enumerate(new, 1):
            item.update(ref=f"n{n}", new=True)
        items = {i["ref"]: i for i in [*old, *new]}
        order = [i["ref"] for i in [*old, *new]]
        if not items:
            return [], [], 0
        prompt = build_prompt(category, old, new, summaries, others)
        for _ in range(2):
            raw = progress.json_object(await generate(prompt))
            if raw is not None and isinstance(raw.get("ops"), list):
                return apply(raw["ops"], items, order, spans, category, others, rank)
        raise ValueError("invalid_reconcile_reply")

    results = await asyncio.gather(*(run(name) for name in memory_contract.CATEGORIES))
    kept = [item for result in results for item in result[0]]
    closed = [item for result in results for item in result[1]]
    value = {
        "format": BUCKET,
        "scope_hash": scope,
        "base": saved["id"] if saved else None,
        "absorbed": [*(saved or {}).get("absorbed", []), *(leaf["id"] for leaf in new_leaves)],
        "items": kept,
        "closed": closed,
        "rejected_ops": sum(result[2] for result in results),
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
