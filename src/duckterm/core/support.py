"""Support: answers questions about using and fixing DuckTerm.

Each question is one model call over the bundled user guide plus this
instance's diagnostics. The guide ships in the public wheel, so it holds only
user-facing material; internal specs stay out of it.
"""

from importlib import resources
from typing import Any

from duckterm.doctor import Result

GUIDE = resources.files("duckterm") / "support" / "guide.md"


def prompt(
    question: str,
    *,
    version: str,
    edition: str,
    features: list[str],
    checks: list[Result],
    prior: list[dict[str, Any]],
) -> str:
    diagnostics = "\n".join(
        f"- {r.status.upper()}: {r.title}" + (f" (fix: {r.detail})" if r.detail else "")
        for r in checks
    )
    history = [f"Q: {item.get('q')}\nA: {item.get('a')}" for item in prior[-2:]]
    return (
        "You are DuckTerm Support. Answer the user's question about using, "
        "setting up or fixing DuckTerm, using only the guide and this "
        "instance's diagnostics below. Give exact commands and menu paths from "
        "the guide. When a diagnostic is WARN or FAIL and bears on the "
        "question, lead with it and its fix. If the guide does not cover the "
        "question, say so and suggest filing a bug report from the app's help "
        "menu instead of guessing. You cannot run commands or change "
        "anything; never say you did.\n\n"
        "The answer renders as Markdown in a narrow chat panel: open with a "
        "one-sentence direct answer, then short steps or bullets. Commands go "
        "in code spans or fenced blocks. No tables, no headings larger than "
        "bold text, no preamble, and no closing offer.\n\n"
        f"Instance: DuckTerm {version}, {edition} edition. "
        f"Features on: {', '.join(features)}.\n"
        f"Diagnostics (duckterm doctor):\n{diagnostics}\n\n"
        f"<guide>\n{GUIDE.read_text()}\n</guide>"
        + ("\n\nEarlier exchanges:\n" + "\n".join(history) if history else "")
        + f"\n\nQuestion: {question}\nAnswer:"
    )
