"""The observation loop for AGENTS.md: turn corrections the user actually gave
agents into proposed typed rules for the folder's shared rule set.

Two correction signals exist today, both already recorded:
  - annotations — a span of an agent's reply the user selected and pushed back
    on ("stop adding comments here").
  - follow-up prompts — every UserPromptSubmit after a session's first prompt.
    The first prompt is the task; later ones are steering.

Corrections carry which runtime (and model, when recorded) they were given to,
so a habit corrected only in codex sessions becomes a codex-scoped rule.

Nothing here runs in the background. The user clicks "Suggest from
corrections" in the AGENTS.md editor, reviews what comes back, and saves —
proposals never land in the rule set on their own.
"""

import re
import time
from dataclasses import dataclass

from duckterm.core.agents_rules import RuleBlock, infer_scope, rule_id
from duckterm.llm.summarizer import summarize


@dataclass
class Correction:
    kind: str  # "annotation" | "follow-up"
    text: str
    runtime: str = ""  # "claude-code" | "codex" | "" when unknown
    model: str = ""  # e.g. "claude-fable-5"; "" when not recorded


_PROMPT = """You maintain AGENTS.md — a short file of durable instructions that \
every coding agent working in one folder reads before starting.

Below are corrections a user gave agents working in this folder: spans of agent \
replies they pushed back on, and mid-session follow-up prompts steering an agent. \
Each is prefixed with the agent runtime it was given to.

Extract at most 5 DURABLE, GENERAL rules worth adding to AGENTS.md. A rule \
qualifies only if it would change how FUTURE, unrelated tasks are done (style, \
tools, conventions, boundaries). Skip anything task-specific, one-off, or \
already covered by the current rules.

Output ONLY the rules, one per line, formatted exactly as:
- [SCOPE] [N] rule text
where SCOPE is "all" when the corrections behind the rule span runtimes, or the \
one runtime they all came from (e.g. "codex"), and N is how many of the listed \
corrections support the rule. If nothing generalizes, output nothing.

Current rules:
{current}

Corrections observed:
{corrections}
"""

_LINE = re.compile(
    r"^- \[(?P<scope>[a-z0-9/._-]+)\]\s*(?:\[(?P<count>\d+)\])?\s*(?P<text>.+)$", re.IGNORECASE
)


def suggest_rules(corrections: list[Correction], current_rules: list[RuleBlock]) -> list[RuleBlock]:
    """Proposed candidate rules from observed corrections, [] when the LLM
    backend is off/unavailable or nothing generalizes. Deduping against the
    existing rule set (including rejected tombstones) is the caller's job via
    merge_candidates — but ids are deterministic, so the same correction
    distilled twice proposes the same id."""
    if not corrections:
        return []
    lines = "\n".join(f"- [{c.runtime or 'unknown'}] [{c.kind}] {c.text}" for c in corrections)
    current = "\n".join(f"- {r.text}" for r in current_rules if r.status == "active")
    prompt = _PROMPT.format(current=current or "(empty)", corrections=lines)
    result = summarize(prompt)

    runtimes = {c.runtime for c in corrections if c.runtime}
    models = {c.model for c in corrections if c.model}
    fallback_scope = infer_scope(runtimes, models) if runtimes else "all"
    today = time.strftime("%Y-%m-%d")

    out: list[RuleBlock] = []
    for raw in result.text.splitlines():
        matched = _LINE.match(raw.strip())
        if not matched:
            continue
        text = matched.group("text").strip()
        scope = matched.group("scope").lower()
        if scope not in {"all", "claude-code", "codex", "copilot"}:
            # The LLM hallucinated a scope — fall back to what the evidence
            # supports as a whole rather than trusting the label.
            scope = fallback_scope
        out.append(
            RuleBlock(
                id=rule_id(text),
                text=text,
                scope=scope,
                status="candidate",
                source="correction",
                evidence=max(1, min(int(matched.group("count") or 1), len(corrections))),
                added=today,
            )
        )
    return out[:5]
