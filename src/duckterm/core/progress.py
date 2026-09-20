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

# The list buckets the UI renders (plus a freeform "summary" paragraph);
# anything else the model returns is dropped. `learnings` is about the WORK;
# `user_learnings` is about the COLLABORATION — how this user prompts, what
# they correct, what keeps going wrong between user and agent.
KEYS = ("deliverables", "learnings", "user_learnings", "next_actions")
_MAX_ITEMS = 8
_MAX_ITEM_CHARS = 160
_MAX_SUMMARY_CHARS = 400
_MAX_TRANSCRIPT_CHARS = 8_000

_PROMPT = """You maintain a running progress digest for an AI coding-agent session.
Update the digest from the prior digest and the recent conversation. Return STRICT JSON only,
no markdown fences, exactly this shape:
{"summary": "...", "deliverables": ["..."], "learnings": ["..."],
 "user_learnings": ["..."], "next_actions": ["..."]}

- summary: 2-3 plain sentences: where the session stands right now
- deliverables: concrete things produced or changed (features, fixes, files, releases)
- learnings: decisions made and constraints discovered about the WORK itself.
  Record the CURRENT decision, not the history of rejected alternatives. Skip
  transient environment hiccups unless they constrain future work.
- user_learnings: recurring collaboration patterns ONLY — a behavior you
  observed at least TWICE, or a preference the user stated outright as a rule.
  A one-off remark or a single decision is an anecdote, not a pattern: leave
  it out. Describe the behavior neutrally ("approves with terse replies —
  proceed immediately"), never characterize personality. Facts about the
  user's life or the project belong in learnings/summary, not here. At most
  4 items; an empty list is better than a stretched one.
- next_actions: what should happen next, most important first
Rules: at most 8 items per list, each item one plain sentence under 120 characters.
Carry forward prior items that are still true; drop next_actions that got done
(they usually become deliverables). Empty lists are fine early on.

PRIOR DIGEST:
{prior}

SESSION GOAL (may be empty):
{goal}

RECENT CONVERSATION (oldest first):
{transcript}
"""


def build_prompt(transcript: list[dict[str, str]], prior: dict[str, Any] | None, goal: str) -> str:
    lines = [f"{r['role']}: {r['text']}" for r in transcript]
    text = "\n".join(lines)[-_MAX_TRANSCRIPT_CHARS:]
    prior_json = json.dumps({k: (prior or {}).get(k, []) for k in KEYS})
    return (
        _PROMPT.replace("{prior}", prior_json)
        .replace("{goal}", goal or "")
        .replace("{transcript}", text or "(no transcript available)")
    )


def parse(text: str) -> dict[str, Any] | None:
    """The summarizer's reply as a clean digest, or None when unusable.
    Tolerates prose/fences around the JSON (models add them despite the
    prompt) by extracting the outermost {...} block."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    digest: dict[str, Any] = {"summary": str(raw.get("summary") or "").strip()[:_MAX_SUMMARY_CHARS]}
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
