"""The observation loop for AGENTS.md: turn corrections the user actually gave
agents into proposed instructions for the folder's shared AGENTS.md.

Two correction signals exist today, both already recorded:
  - annotations — a span of an agent's reply the user selected and pushed back
    on ("stop adding comments here").
  - follow-up prompts — every UserPromptSubmit after a session's first prompt.
    The first prompt is the task; later ones are steering.

Nothing here runs in the background. The user clicks "Suggest from
corrections" in the AGENTS.md editor, reviews what comes back, edits, and
saves — proposals never land in the file on their own.
"""

from dataclasses import dataclass

from duckterm.llm.summarizer import summarize


@dataclass
class Correction:
    kind: str  # "annotation" | "follow-up"
    text: str


_PROMPT = """You maintain AGENTS.md — a short file of durable instructions that \
every coding agent working in one folder reads before starting.

Below are corrections a user gave agents working in this folder: spans of agent \
replies they pushed back on, and mid-session follow-up prompts steering an agent.

Extract at most 5 DURABLE, GENERAL rules worth adding to AGENTS.md. A rule \
qualifies only if it would change how FUTURE, unrelated tasks are done (style, \
tools, conventions, boundaries). Skip anything task-specific, one-off, or \
already covered by the current AGENTS.md.

Output ONLY the rules, one per line, each starting with "- ". If nothing \
generalizes, output nothing.

Current AGENTS.md:
{current}

Corrections observed:
{corrections}
"""


def suggest_rules(corrections: list[Correction], current_md: str) -> list[str]:
    """Proposed AGENTS.md lines from observed corrections, [] when the LLM
    backend is off/unavailable or nothing generalizes."""
    if not corrections:
        return []
    lines = "\n".join(f"- [{c.kind}] {c.text}" for c in corrections)
    prompt = _PROMPT.format(current=current_md.strip() or "(empty)", corrections=lines)
    result = summarize(prompt)
    return [ln.strip() for ln in result.text.splitlines() if ln.strip().startswith("- ")]
