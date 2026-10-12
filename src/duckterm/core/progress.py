"""Running progress digest for a session: deliverables / learnings / next
actions, regenerated every few turns and stored on the session row so it's
always available in the dashboard (and survives restarts).

The digest is one summarizer call over the recent transcript plus the prior
digest ("carry forward what's still true"), so cost stays one small LLM call
per few turns per active session — the server debounces triggers.
"""

import json
import re
from typing import Any

from duckterm import prompts

# The list buckets the UI renders (plus a freeform "summary" paragraph);
# anything else the model returns is dropped. `learnings` is about the WORK;
# `user_learnings` is about the COLLABORATION — how this user prompts, what
# they correct, what keeps going wrong between user and agent.
KEYS = ("deliverables", "learnings", "user_learnings", "next_actions")
_MAX_ITEMS = 8
_MAX_ITEM_CHARS = 160
_MAX_SUMMARY_CHARS = 2000
_MAX_TRANSCRIPT_CHARS = 8_000

_PROMPT = prompts.load("digest_write")


def build_prompt(transcript: list[dict[str, str]], prior: dict[str, Any] | None, goal: str) -> str:
    lines = [f"{r['role']}: {r['text']}" for r in transcript]
    text = "\n".join(lines)[-_MAX_TRANSCRIPT_CHARS:]
    prior_json = json.dumps({k: (prior or {}).get(k, []) for k in ("summary", *KEYS)})
    return prompts.render(
        "digest_write",
        prior=prior_json,
        goal=goal or "",
        transcript=text or "(no transcript available)",
    )


def parse(text: str) -> dict[str, Any] | None:
    """The summarizer's reply as a clean digest, or None when unusable.
    Tolerates prose/fences around the JSON (models add them despite the
    prompt) by extracting the outermost {...} block."""
    raw = json_object(text)
    if raw is None:
        return None
    summary = str(raw.get("summary") or "").strip()
    if len(summary) > _MAX_SUMMARY_CHARS:
        # This text is reviewed and saved, not a display preview. Slicing it can
        # cut off a sentence or qualification and manufacture a rejected draft.
        return None
    digest: dict[str, Any] = {"summary": summary}
    for key in KEYS:
        items = raw.get(key)
        if not isinstance(items, list):
            digest[key] = []
            continue
        digest[key] = [str(i).strip()[:_MAX_ITEM_CHARS] for i in items if str(i).strip()][
            :_MAX_ITEMS
        ]
    if not digest["summary"] and not any(digest[k] for k in KEYS):
        return None
    return digest


def parse_diagnostic(text: str) -> str:
    """Fixed failure codes for the existing bounded draft-correction path."""
    raw = json_object(text)
    if raw is not None and len(str(raw.get("summary") or "").strip()) > _MAX_SUMMARY_CHARS:
        return "summary_too_long"
    return "invalid_digest"


def json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object without weakening the schema checks of its caller."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    return raw


# ── validation: L1 (does each item make sense on its own) + L2 (compare
#    against what's already stored) — one summarizer call for both ──

_VALIDATE_PROMPT = prompts.load("digest_validate")


def candidate_items(digest: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten a parsed digest into (bucket, text) items for validation."""
    return [{"bucket": bucket, "text": text} for bucket in KEYS for text in digest.get(bucket, [])]


def validate_prompt(
    digest: dict[str, Any],
    existing: list[dict[str, Any]],
    *,
    source: str = "",
    required: str = "",
) -> str:
    cand = json.dumps(candidate_items(digest))
    rows = (
        "\n".join(f"{r['id']} | {r['bucket']} | {r['status']} | {r['text']}" for r in existing)
        or "(none yet)"
    )
    return prompts.render(
        "digest_validate",
        candidate=cand,
        existing=rows,
        summary=str(digest.get("summary") or ""),
        source=source,
        required=required,
    )


def parse_verdicts(text: str) -> dict[str, Any] | None:
    """The validator's reply, or None when unusable (caller falls back to
    the code-only merge — validation failing must never lose a digest)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("accept"), list):
        return None
    accept = [
        {"bucket": str(i.get("bucket", "")), "text": str(i.get("text", "")).strip()}
        for i in raw["accept"]
        if isinstance(i, dict) and str(i.get("text", "")).strip()
    ]
    done_raw = raw.get("done_next_action_ids", [])
    if not isinstance(done_raw, list):
        return None
    done = [str(i) for i in done_raw if str(i).strip()]
    validation = raw.get("summary_validation")
    return {"accept": accept, "done_next_action_ids": done, "summary_validation": validation}


def summary_review(validation: Any) -> dict[str, Any]:
    """Keep review outcomes distinct, without retaining arbitrary provider prose."""
    if validation is None:
        return {"state": "missing_verdict", "reason_codes": []}
    if not isinstance(validation, dict) or type(validation.get("ready")) is not bool:
        return {"state": "invalid_verdict", "reason_codes": []}
    codes = validation.get("reason_codes")
    if (
        not isinstance(codes, list)
        or len(codes) > 16
        or any(
            not isinstance(c, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", c) for c in codes
        )
    ):
        return {"state": "invalid_reason_codes", "reason_codes": []}
    if validation["ready"] is False:
        return {"state": "rejected", "reason_codes": list(codes)}
    if codes:
        return {"state": "inconsistent_verdict", "reason_codes": list(codes)}
    return {"state": "accepted", "reason_codes": []}


def review_diagnostic(review: dict[str, Any]) -> str:
    return (
        "review_"
        + str(review["state"])
        + (":" + ",".join(review["reason_codes"]) if review["reason_codes"] else "")
    )


def review_outcome(text: str, verdicts: dict[str, Any] | None) -> dict[str, Any]:
    if verdicts is not None:
        return summary_review(verdicts.get("summary_validation"))
    # A malformed digest envelope can still contain a readable rejection.
    # Preserve its valid reason codes without accepting the broken response.
    raw = json_object(text)
    review = summary_review(raw.get("summary_validation") if raw is not None else None)
    return {"state": "malformed_reply", "reason_codes": review["reason_codes"]}


def fallback_verdicts(digest: dict[str, Any]) -> dict[str, Any]:
    """Code-only merge when the validator call fails: accept everything (the
    store's normalized-text dedup still applies), complete nothing."""
    return {"accept": candidate_items(digest), "done_next_action_ids": []}
