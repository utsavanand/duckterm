"""One continuation budget with explicit gaps and intact protected claims/tools."""

from __future__ import annotations

import json
from contextlib import suppress
from copy import deepcopy
from typing import Any

from duckterm import prompts
from duckterm.core.session_api import APIError

MAX_BRIEF_BYTES = 32000


def render(
    *,
    introduction: str,
    launch: dict[str, Any],
    required: str,
    protected: dict[str, Any],
    narratives: list[dict[str, Any]],
    metadata: dict[str, Any],
    source_map: list[dict[str, str]],
    tools: str,
) -> str:
    """Omit whole optional units, declare them, and preserve all open claims."""
    from duckterm import memory_contract

    original = deepcopy(metadata)
    coverage = memory_contract.read_coverage(original.get("coverage"))
    kept_map, kept_narratives = source_map, narratives
    omitted_fields: list[str] = []
    required_value: Any = None
    with suppress(ValueError):
        required_value = json.loads(required)

    def candidate() -> tuple[str, dict[str, Any]]:
        info = deepcopy(original)
        gaps = []
        sections: dict[str, Any] = {}
        for name, removed, ref_key in (
            ("source_references", source_map[len(kept_map) :], "source"),
            (
                "summary_narratives",
                narratives[: len(narratives) - len(kept_narratives)],
                "revision",
            ),
        ):
            if removed:
                sections[name] = len(removed)
                gaps.append(
                    {
                        "reason": "budget_exhausted",
                        "count": len(removed),
                        "sample_refs": [
                            item[ref_key]
                            for item in removed
                            if isinstance(item.get(ref_key), str) and 0 < len(item[ref_key]) <= 160
                        ][:8],
                    }
                )
        if omitted_fields:
            sections["current_work_fields"] = list(omitted_fields)
            gaps.append(
                {
                    "reason": "budget_exhausted",
                    "count": len(omitted_fields),
                    "sample_refs": [
                        item["source"] for item in source_map if item.get("kind") == "session"
                    ][: min(8, len(omitted_fields))],
                }
            )
        if gaps:
            info["coverage"] = {
                **coverage,
                "declared": True,
                "omitted": coverage["omitted"] + gaps,
            }
            info["handoff_omissions"] = sections
        heading = prompts.render(
            "handoff_continuation",
            introduction=introduction,
            launch=json.dumps(launch, ensure_ascii=False),
            required=required,
            summary=json.dumps(protected, ensure_ascii=False),
            tools=tools,
        )
        text = (
            heading
            + "\nRecent summary narratives (oldest first):\n"
            + json.dumps(kept_narratives, ensure_ascii=False)
            + "\nSource references (memory sources lists the full catalog):\n"
            + json.dumps(kept_map, ensure_ascii=False)
            + "\nCoverage:\n"
            + json.dumps(info, ensure_ascii=False)
        )
        return text, info

    def fits() -> bool:
        return len(candidate()[0].encode()) <= MAX_BRIEF_BYTES

    if not fits():
        # Prefer narratives over a catalog that the supplied tools can list.
        lo, hi = 0, len(source_map)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            kept_map = source_map[:mid]
            if fits():
                lo = mid
            else:
                hi = mid - 1
        kept_map = source_map[:lo]
    if not fits():
        # Whole latest narratives survive; never cut a sentence or a claim.
        lo, hi = 0, len(narratives)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            kept_narratives = narratives[len(narratives) - mid :]
            if fits():
                lo = mid
            else:
                hi = mid - 1
        kept_narratives = narratives[len(narratives) - lo :]
    if not fits() and isinstance(required_value, dict):
        # These are retrievable current records, not the session's identity,
        # role, launch expectations, or protected claims. Omit complete fields.
        for field in ("notes", "mail", "tasks"):
            if field not in required_value or not required_value[field]:
                continue
            omitted_fields.append(field)
            required_value.pop(field)
            required = json.dumps(required_value, ensure_ascii=False)
            if fits():
                break
        if fits() and omitted_fields:
            # Removing a large retrievable field can free room again. Restore
            # whole recent narratives, then catalog pointers, in that order.
            for count in range(1, len(narratives) + 1):
                kept_narratives = narratives[-count:]
                if not fits():
                    kept_narratives = narratives[len(narratives) - count + 1 :]
                    break
            lo, hi = 0, len(source_map)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                kept_map = source_map[:mid]
                if fits():
                    lo = mid
                else:
                    hi = mid - 1
            kept_map = source_map[:lo]
    text, info = candidate()
    if len(text.encode()) > MAX_BRIEF_BYTES:
        kinds = sorted(
            {
                str(c.get("kind", field))
                for field, entries in protected.items()
                if isinstance(entries, list)
                for c in entries
                if isinstance(c, dict)
            }
        )
        raise APIError(
            409,
            "Protected claims and required onboarding exceed the 32,000-byte handoff budget"
            + ("; protected kinds: " + ", ".join(kinds) if kinds else "")
            + ". Source agent kept; no handoff was saved",
        )
    # The caller saves/returns exactly the coverage delivered in the brief.
    # A refusal leaves the original metadata and all inputs untouched.
    metadata.clear()
    metadata.update(info)
    return text
